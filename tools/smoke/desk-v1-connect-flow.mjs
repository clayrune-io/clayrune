#!/usr/bin/env node
/**
 * Desk v1 — connect by address (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 1), the browser half,
 * against a fake server, `desk_v1_live` ON. The server half is pinned by tests/test_desk_connect.py.
 *
 * The four steps, at 1440 and at 390:
 *   1. Address  the Add service tile opens the numbered flow; a refused address shows the server's
 *               reason and hint; an accepted one shows the normalised host.
 *   2. Method   a known host (X) is Recognised and its guide can be opened; an unknown host says "Not
 *               recognised yet"; MCP is Information only; only "Save for agents" can be chosen, and
 *               Continue stays off until it is.
 *   3. Details  a name and an optional vault credential typed with the shared form (secret-form.js).
 *               NOTHING is sent to the server before Save (no /commit, no /api/secrets); the password sits
 *               in its own <input> and in no storage; Back and a repaint keep what was typed.
 *   4. Review   exactly what Save will write, the value shown nowhere; Save asks for the passcode;
 *               a wrong passcode keeps the draft; the right one POSTs /commit exactly once, the form
 *               is emptied and the saved service is selected.
 *   + phone     no horizontal scroll, Back and the action are inside the viewport at every step.
 *   + demo      desk_v1_live OFF: the flow is not offered (the preview has no server to ask).
 *
 * Screenshots: docs/desk_v1/screens/connect_flow_{url,method,details,review,method_linkedin,details_nocred,review_nocred}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-flow.mjs
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
// registry.json is an index since P1; the screens' rows come from the loaded profiles, in the version 1 shape.
const REGISTRY = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c',
  `import json, sys; sys.path.insert(0, ${JSON.stringify(REPO_ROOT)}); from mc.desk_connect import registry; print(json.dumps(registry.v1_projection()))`],
  { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const PASSCODE = 'right-passcode';
const SECRET = 'sk-test-9f3a71c2d4e8b6a0';

// What the server's inspect answers, built from the real registry file the way methods.inspect does.
function inspectFake(raw) {
  let u;
  try { u = new URL(raw); } catch (_) { return { status: 400, body: { error: 'That is not a web address.', hint: 'Paste it with https://, for example https://plausible.io.' } }; }
  if (u.protocol !== 'https:') return { status: 400, body: { error: 'Only https addresses can be used.', hint: 'Paste the https:// address of the service.' } };
  const host = u.hostname.toLowerCase();
  const svc = REGISTRY.services.find((s) => s.hosts.includes(host)) || null;
  const rows = (svc ? svc.options : []).concat(REGISTRY.common_options, [{
    method: 'save_for_agents', support: 'available', title: 'Save for agents', evidence: 'Always available',
    guidance: 'Saves a note for agents: the name and address, plus the name of a login if you add one. Clayrune does not connect to it, sign in to it or post to it.',
  }]).map((o) => ({ ...o, selectable: o.method === 'save_for_agents' }));
  return { status: 200, body: { url: `https://${host}${u.pathname === '/' ? '' : u.pathname}`, host, path: u.pathname,
    service: svc ? { id: svc.id, label: svc.label } : null, options: rows } };
}

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, services: [], seen: new Set(),
    engines: [
      { id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', auth: { kind: 'oauth', vault_entry: 'x' }, job_limit_usd: null,
        connected: { ready: false, vault_entry: null, reason: 'not signed in' }, models: [{ model_id: 'm1', kind: 'video', label: 'M1', status: 'stable', aspect_ratios: ['16:9'] }] },
    ] };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'blog')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  return srv;
}

async function newPage(browser, { live, srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
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
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    // Any request that could write a secret outside the one Save is recorded and refused.
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: srv.engines });
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const r = inspectFake((body || {}).input);
      return J(r.body, r.status);
    }
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.seen.has(body.request_id)) return J({ ok: true, duplicate: true, service: srv.services[srv.services.length - 1], credential: null }, 200);
      srv.seen.add(body.request_id);
      const d = body.draft;
      const sv = { id: 'svc-' + srv.services.length, name: d.name, link: d.url, kind: 'saved_for_agents', publish: false,
        credential: { name: d.credential ? d.credential.name : '', in_vault: d.credential ? true : null } };
      srv.services.push(sv);
      return J({ ok: true, duplicate: false, service: sv, credential: d.credential ? { name: d.credential.name, stored: true } : null }, 201);
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
const commits = (srv) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/desk/connect/commit');
const step = (page) => page.$eval('[data-cf]', (e) => e.dataset.cfStep).catch(() => null);
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_flow_${name}_${w}.png`) });

// No horizontal scroll anywhere that matters, and the step's Back / action inside the visible window.
async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    const bar = document.querySelector('[data-cf-actions]');
    const r = bar ? bar.getBoundingClientRect() : null;
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
      bar: r ? { left: r.left, right: r.right, bottom: r.bottom, top: r.top } : null, vw: window.innerWidth, vh: window.innerHeight };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  if (m.bar) check(m.bar.left >= 0 && m.bar.right <= m.vw && m.bar.bottom <= m.vh + 1 && m.bar.top >= 0,
    `${label}: Back and the action are inside the ${m.vw}x${m.vh} window`, `${label}: action bar outside the window ${JSON.stringify(m.bar)} in ${m.vw}x${m.vh}`);
}

async function flow(browser, width, height) {
  console.log(`live ON: the four steps at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv, width, height });
  await page.waitForSelector('[data-conn-tile="ch-x-ron"]', { timeout: 6000 }).catch(() => {});

  // ── 1. Address ──
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  const stepItems = await page.$$eval('[data-cf-step-item]', (els) => els.map((e) => e.dataset.cfStepItem));
  check(stepItems.join() === 'url,method,details,review', `Add service opens the numbered flow (${stepItems.join(' > ')})`, 'steps: ' + stepItems.join());
  check(!!(await page.$('[data-add-list]')), 'the existing list is still offered below the address step', 'the list is gone');
  const noPw = await page.$$('[data-connections] input[type="password"]');
  check(noPw.length === 0, 'no credential field exists until Details', 'a password field is on the screen at step 1');
  await shot(page, 'url', width);
  await fits(page, 'address step');

  await page.fill('[data-cf-url]', 'http://plausible.io');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf-msg="error"]', { timeout: 4000 });
  const refusal = await page.textContent('[data-cf-msg="error"]');
  check(/https/i.test(refusal) && (await step(page)) === 'url', `a refused address stays on step 1 and says why ("${refusal.trim().slice(0, 70)}")`, 'refusal text: ' + refusal);

  // ── 2. Method: a known host ──
  await page.fill('[data-cf-url]', 'https://x.com/ron');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  check((await page.textContent('[data-cf-host]')).includes('x.com'), 'the normalised host is shown (x.com)', 'host: ' + (await page.textContent('[data-cf-host]')));
  check((await page.getAttribute('[data-cf-known]', 'data-cf-known')) === 'x', 'X is Recognised', 'known: ' + (await page.getAttribute('[data-cf-known]', 'data-cf-known')));
  const xRows = await page.$$eval('[data-cf-option]', (els) => els.map((e) => [e.dataset.cfOption, e.dataset.support]));
  check(xRows.some((r) => r[0] === 'oauth' && r[1] === 'available') && xRows.some((r) => r[0] === 'mcp' && r[1] === 'info_only') && xRows.some((r) => r[0] === 'save_for_agents'),
    `X lists its sign-in, MCP as information only, and Save for agents (${JSON.stringify(xRows)})`, 'x rows: ' + JSON.stringify(xRows));
  await page.click('[data-cf-open="account:x"]');
  await page.waitForSelector('[data-conn-add]', { timeout: 4000 });
  check((await page.textContent('[data-conn-add] .desk-v1-rules-group-title')).includes('X account'), '"Open the guide" hands over to the existing X account form', 'the X form did not open');
  check(!(await page.$('[data-cf]')), 'leaving through a guide drops the flow', 'the flow stayed');
  await page.click('[data-add-back]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });

  // ── 2. Method: an unknown host ──
  await page.fill('[data-cf-url]', 'https://plausible.io/mysite.example');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  check(/Not recognised yet/.test(await page.textContent('[data-cf-known]')), 'an unknown host says "Not recognised yet"', 'known text: ' + (await page.textContent('[data-cf-known]')));
  const mcp = await page.$eval('[data-cf-option="mcp"] .desk-v1-cf-badge', (e) => e.textContent.trim());
  check(mcp === 'Information only' && !(await page.$('[data-cf-option="mcp"] input')), 'MCP reads "Information only" and cannot be chosen', 'mcp badge: ' + mcp);
  check(await page.$eval('[data-cf-next]', (b) => b.disabled), 'Continue is off until a method is chosen', 'Continue was on with no method');
  await shot(page, 'method', width);
  await fits(page, 'method step');
  await page.check('[data-cf-method]');
  check(!(await page.$eval('[data-cf-next]', (b) => b.disabled)), 'choosing Save for agents turns Continue on', 'Continue stayed off');
  await page.click('[data-cf-next]');

  // ── 3. Details ──
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  check((await page.inputValue('[data-cf-name]')) === 'plausible.io', 'the name starts as the host', 'name: ' + (await page.inputValue('[data-cf-name]')));
  await page.fill('[data-cf-name]', 'Plausible analytics');
  await page.check('[data-cf-usecred]');
  await page.waitForSelector('#cf-value', { state: 'visible', timeout: 4000 });
  check((await page.inputValue('#cf-name')) === 'plausible.api-key', 'the credential name is suggested from the service (plausible.api-key)', 'cred name: ' + (await page.inputValue('#cf-name')));
  check((await page.$$('#cf-user-block:not([hidden])')).length === 0, 'an API key has no username slot', 'a username slot is showing for an API key');
  check((await page.$$('#cf-2fa-help:not([hidden])')).length === 0, 'the 2FA-seed help is not offered (the flow cannot import one)', '2FA help is visible');
  await page.fill('#cf-value', SECRET);
  await page.fill('#cf-desc', 'Plausible stats key');
  const dNote = await page.$eval('[data-cf-d-note]', (e) => e.textContent.trim());
  check(dNote === 'This saves a note for agents: the name and address, plus a login if you add one. Clayrune does not connect to it.', 'Details says plainly that it saves a note and does not connect', 'details note: ' + dNote);
  check(await page.evaluate(() => { const n = document.querySelector('[data-cf-d-note]'), c = document.querySelector('[data-cf-usecred]'); return !!(n.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING); }), 'the note sits above the credential checkbox', 'the note is not above the checkbox');
  check(/agents may use, for example in the browser pane/.test(await page.textContent('.desk-v1-cf-check')), 'the checkbox hint says the credential is a login agents may use', 'checkbox hint: ' + (await page.textContent('.desk-v1-cf-check')));
  check(/not ways to connect/.test(await page.textContent('[data-cf-d-typehint]')), 'the type list is labelled as kinds of secret, not ways to connect', 'type hint missing');
  await shot(page, 'details', width);
  await fits(page, 'details step');
  const before = srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|purposes)/.test(r.path));
  check(before.length === 0, 'nothing has been written or posted yet (only inspect was called)', 'writes before Save: ' + JSON.stringify(before.map((r) => r.path)));

  // Back keeps the form.
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  check((await page.inputValue('#cf-value')) === SECRET && (await page.inputValue('[data-cf-name]')) === 'Plausible analytics',
    'Back and forward keep the typed name and password', 'the form lost its contents going Back');
  // A repaint from elsewhere (the engines list reloading) keeps it too.
  await page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));
  await page.waitForSelector('#cf-value', { timeout: 4000 });
  check((await page.inputValue('#cf-value')) === SECRET, 'a repaint of the screen keeps the password', 'a repaint wiped the password');

  await page.click('[data-cf-next]');

  // ── 4. Review ──
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  const facts = await page.$eval('[data-cf-review]', (e) => e.innerText);
  check(/Plausible analytics/.test(facts) && /plausible\.io/.test(facts) && /Save for agents/.test(facts), 'Review shows the name, the address and the method', 'review: ' + facts);
  check(/Connects\s*No\. Agents see this record only\./.test(facts), 'Review has a Connects row: "No. Agents see this record only."', 'review: ' + facts);
  const credFacts = await page.$eval('[data-cf-r-credbox]', (e) => e.innerText);
  check(/plausible\.api-key/.test(credFacts) && /API key/.test(credFacts) && /hidden/.test(credFacts), 'Review shows the credential\'s name and type, the value hidden', 'credential facts: ' + credFacts);
  check(!(await page.evaluate((s) => document.body.innerText.includes(s) || document.body.innerHTML.includes(s), SECRET)), 'the password appears nowhere in the page text or markup', 'the password is in the DOM text');
  const store = await page.evaluate((s) => JSON.stringify([localStorage, sessionStorage]).includes(s) || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(s), SECRET);
  check(!store, 'the password is in neither localStorage nor sessionStorage', 'the password reached browser storage');
  await shot(page, 'review', width);
  await fits(page, 'review step');

  // Wrong passcode keeps the draft.
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', 'wrong-passcode');
  await page.click('.modal-content .btn-add');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  check(commits(srv).length === 1 && commits(srv)[0].body.passcode === 'wrong-passcode', 'a wrong passcode reaches the server once and is refused', 'commit log: ' + JSON.stringify(commits(srv).map((c) => c.body && c.body.passcode)));
  await page.click('.modal-content .btn-secondary');   // cancel the prompt
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  check(srv.services.length === 0 && (await page.inputValue('#cf-value')) === SECRET, 'after a wrong passcode nothing is saved and the draft (password included) is still there', 'draft lost or saved: ' + srv.services.length);

  // Right passcode.
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
  await page.waitForSelector('[data-conn-tile^="service:"][aria-pressed="true"]', { timeout: 6000 });
  const sent = commits(srv);
  const right = sent[sent.length - 1];
  check(sent.length === 2 && right.body.passcode === PASSCODE && right.body.draft.method === 'save_for_agents' && right.body.draft.url === 'https://plausible.io/mysite.example'
    && right.body.draft.credential.name === 'plausible.api-key' && right.body.draft.credential.value === SECRET && right.body.request_id.length >= 8,
    'Save posts one commit with the request id, the draft and the passcode', 'commit: ' + JSON.stringify(right.body).replace(SECRET, '***'));
  check(sent[0].body.request_id === right.body.request_id, 'a retry after a wrong passcode reuses the same request id', 'request ids differ: ' + sent.map((c) => c.body.request_id).join());
  check(srv.services.length === 1 && (await page.$eval('[data-conn-detail] [data-conn-status]', (e) => e.textContent)).includes('Saved for agents'), 'the saved service is selected and reads "Saved for agents"', 'service state wrong');
  check(!(await page.$('#cf-value')) && !(await page.$('[data-cf]')), 'the credential form is gone once the save is accepted', 'the form is still in the page');
  check(!srv.log.some((r) => r.path.startsWith('/api/secrets')), 'the credential went through the one commit, not through the Secrets routes', 'a /api/secrets request was made');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

// A recognised service with no usable route (LinkedIn): the Method step names the missing route and says
// bringing your own MCP server or API is not available yet; with no credential, Review still says Connects: No.
async function missingRoute(browser, width, height) {
  console.log(`live ON, LinkedIn at ${width}px: the missing route is named`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'https://www.linkedin.com/company/clayrune');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  const miss = await page.$eval('[data-cf-missing]', (e) => e.textContent.trim());
  check(/No way to connect LinkedIn is open yet/.test(miss) && /LinkedIn Company Page posting/.test(miss) && /approved apps/.test(miss) && /own MCP server or API for it is not available yet; it is coming\./.test(miss),
    'LinkedIn names the missing route from its guidance and says your own MCP/API is not available yet', 'missing text: ' + miss);
  check(!/^Information only:/.test(miss.split('Company Page posting. ')[1] || ''), 'the "Information only:" prefix is not repeated in the sentence', 'prefix repeated: ' + miss);
  check((await page.$$('[data-cf-missing] button, [data-cf-missing] a, [data-cf-missing] input')).length === 0, 'the note has no button or option', 'the note has a control');
  check((await page.$$eval('[data-cf-method]', (els) => els.map((e) => e.value))).join() === 'save_for_agents', 'Save for agents is still the only choosable row', 'rows choosable: other');
  await shot(page, 'method_linkedin', width);
  await fits(page, 'LinkedIn method step');
  await page.check('[data-cf-method]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  check(!(await page.$eval('[data-cf-usecred]', (e) => e.checked)), 'the credential box is unticked', 'ticked');
  await shot(page, 'details_nocred', width);
  await fits(page, 'LinkedIn details step');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  const facts = await page.$eval('[data-cf-review]', (e) => e.innerText);
  check(/Connects\s*No\. Agents see this record only\./.test(facts), 'Review says Connects: No', 'review: ' + facts);
  check(/No credential: agents see the service, with nothing to sign in with\./.test(await page.$eval('[data-cf-r-credbox]', (e) => e.innerText)), 'the no-credential line is kept', 'no-credential line gone');
  await shot(page, 'review_nocred', width);
  await fits(page, 'LinkedIn review step');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function closeClears(browser) {
  console.log('live ON: leaving the flow empties the form');
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv, width: 1440, height: 900 });
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'https://plausible.io');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf-usecred]', { timeout: 4000 });
  await page.check('[data-cf-usecred]');
  await page.fill('#cf-value', SECRET);
  await page.click('[data-conn-detail-close]');
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  check(!(await page.$('#cf-value')) && (await page.inputValue('[data-cf-url]')) === '', 'closing the panel throws the draft and the password away', 'the draft survived closing');
  await page.fill('[data-cf-url]', 'https://plausible.io');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method]');
  await page.click('[data-cf-next]');
  await page.check('[data-cf-usecred]');
  check((await page.inputValue('#cf-value')) === '', 'a new flow starts with an empty password', 'an old password came back');
  check(commits(srv).length === 0, 'no commit was ever sent', 'a commit was sent');
  await ctx.close();
}

async function demoOff(browser) {
  console.log('demo (desk_v1_live OFF): the flow is not offered');
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv, width: 1440, height: 900 });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-add-list]', { timeout: 4000 });
  check(!(await page.$('[data-cf]')) && !!(await page.$('[data-add-list]')), 'the preview shows only the existing list', 'the flow showed in the preview');
  check(srv.log.filter((r) => r.path.startsWith('/api/desk/connect')).length === 0, 'the preview made no connect request', 'a connect request was made');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await flow(browser, 1440, 900);
  await flow(browser, 390, 844);
  await missingRoute(browser, 1440, 900);
  await missingRoute(browser, 390, 844);
  await closeClears(browser);
  await demoOff(browser);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
