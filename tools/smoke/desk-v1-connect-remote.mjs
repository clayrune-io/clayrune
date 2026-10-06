#!/usr/bin/env node
/**
 * Desk v1 — "Add a remote MCP server" and its approval card (docs/DESK_SERVICE_PROFILES_SPEC.md sections
 * 6.1 and 6.3, slice U2d), the browser half, against a fake server, `desk_v1_live` ON. The server half is
 * pinned by tests/test_desk_connect_remote.py; the card here has the keys remote_mcp_service._card builds.
 * Its own file (not an extension of desk-v1-connect-custom.mjs): the remote card is its own module.
 *
 *   project    Review of an https address sends only the typed fields (a header NAME and a Secrets NAME, no
 *              value), shows the exact address, recipient, reach, a plain remote_server_can_change label and
 *              "nothing has been sent to this server"; Save is off until approved; the Save carries only
 *              {request_id, fingerprint, passcode}; nothing contacts the server until the person asks.
 *   check      a check the person starts shows what the server answered and never says verified; a changed
 *              server shows its diff and offers a passcode-gated accept.
 *   exposure   http:// and a local address each need their own tick ON the card before approval can turn on.
 *   edits      changing a field drops the card and its approval.
 *   + phone    no horizontal scroll, the Save button reachable at 390px.
 *
 * Screenshots: docs/desk_v1/screens/connect_remote_{card,exposure,checked}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-remote.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createHash } from 'node:crypto';
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
const sha = (s) => 'sha256:' + createHash('sha256').update(s).digest('hex');

function makeServer({ checkOutcome = 'baseline_recorded' } = {}) {
  const fx = loadFixtures();
  const srv = { log: [], fx, n: 0, cards: new Map(), checkOutcome };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'linkedin')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  srv.card = (b) => {
    const url = b.url.replace(/\/+$/, '') || b.url;
    const u = new URL(url);
    const plain = u.protocol === 'http:';
    const local = ['localhost', '127.0.0.1'].includes(u.hostname);
    const required = [].concat(plain ? ['unencrypted_connection'] : []).concat(local ? ['local_or_private_target'] : []);
    const acknowledged = (b.acknowledge || []).filter((f) => required.includes(f));
    const missing = required.filter((f) => !acknowledged.includes(f));
    const protocol = b.protocol || (u.pathname.endsWith('/sse') ? 'sse' : 'streamable_http');
    const name = b.server_name || u.hostname.replace(/\./g, '-');
    const project = b.scope === 'project' ? fx.projects.find((p) => p.id === b.project_id) : null;
    const fingerprint = sha(JSON.stringify({ url, protocol, name, scope: b.scope, creds: b.credentials || [], ack: acknowledged }));
    const recipient = `${u.protocol}//${u.host}`;
    const creds = (b.credentials || []).map((c) => ({ header: c.header, vault: c.vault, prefix: c.prefix || '', env: 'MC_REMOTE_HEADER_0', placement: `HTTP header ${c.header}`, recipient }));
    const previous = srv.previous || null;
    const changes = previous && previous.scope !== b.scope ? [{ field: 'reach', from: previous.scope, to: b.scope }] : [];
    return {
      schema: 'desk-custom-card/1', kind: 'remote', request_id: 'req-' + (++srv.n) + '-abcdefghijklmnop', fingerprint,
      origin: { code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' },
      title: recipient, server_name: name, protocol, protocol_proposed: { value: u.pathname.endsWith('/sse') ? 'sse' : 'streamable_http', basis: u.pathname.endsWith('/sse') ? 'the address ends in /sse' : 'the address does not end in /sse' },
      url, recipient, auth: b.auth === 'oauth' ? { type: 'oauth', issuer: b.issuer, scopes: b.scopes || [] } : { type: b.auth === 'header' ? 'header' : 'none' },
      command: { command: '/usr/bin/python3', args: ['-I', '/clayrune/tools/remote-mcp-bridge.py', '--protocol', protocol, '--url', url], runnable: true },
      credentials: creds,
      exposure: { unencrypted: plain, local_or_private: local, required, acknowledged, missing },
      reach: project ? { scope: 'project', project: { id: project.id, name: project.name }, who: `Agents working in the project "${project.name}" only.`, secrets_to: creds.length ? recipient : 'none' }
        : { scope: 'global', project: null, who: 'Agents in every project.', secrets_to: creds.length ? recipient : 'none' },
      scope_default: 'project', scope_options: ['project', 'global'], scope_chosen: b.scope,
      contact: { before_save: 'none', text: 'Nothing has been sent to this server: no connection, no consent request, no initialize. The protocol above is a proposal from the address alone.' },
      verification: { purpose_verified: false, text: 'Saving does not check that the server works. A check you start after saving can confirm it answers and records the tools it offers; it never counts as verifying a purpose.' },
      risks: [{ code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' }, { code: 'remote_server_can_change', label: 'Remote server can change without a version pin. Its tools and behavior are whatever it serves when it is contacted.' }]
        .concat(creds.length ? [{ code: 'secrets_to_remote', label: `The Secrets entries listed are sent as HTTP headers to ${recipient} and to no other address.` }] : [])
        .concat(required.map((f) => ({ code: f, label: f })))
        .concat(b.scope === 'global' ? [{ code: 'global_reach', label: "Global: agents in EVERY project can use this server's tools." }] : [])
        .concat([{ code: 'not_purpose_verified', label: 'Reaching the server shows that it answers. It does not show that it does what you want it for.' }]),
      limitations: [], changes, reask: changes.length > 0, replaces: changes.length ? sha('old') : null, approved: false,
    };
  };
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
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const u = new URL(body.input);
      return J({ url: `https://${u.hostname}`, host: u.hostname, path: '/', service: null, input_kind: 'url',
        options: [{ method: 'save_for_agents', support: 'available', title: 'Save for agents', evidence: 'Always available', guidance: 'Saves a note for agents.', selectable: true }] });
    }
    if (path === '/api/desk/connect/custom/remote/review' && method === 'POST') {
      if (!body.url) return J({ error: 'enter the address of the server', code: 'bad_url' }, 400);
      const card = srv.card(body);
      srv.cards.set(card.request_id, card);
      return J(card);
    }
    if (path === '/api/desk/connect/custom/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const card = srv.cards.get(body.request_id);
      if (!card || card.fingerprint !== body.fingerprint) return J({ error: 'the configuration changed since you reviewed it. Review it again.', code: 'changed_since_review' }, 409);
      srv.saved = card;
      return J({ ok: true, approved: true, duplicate: false, fingerprint: card.fingerprint, server_name: card.server_name, scope: card.scope_chosen,
        project_id: card.reach.project ? card.reach.project.id : null, state: 'registered', code: '', message: 'Registered. It starts the first time an agent session uses it.' }, 201);
    }
    if (path === '/api/desk/connect/custom/remote/check' && method === 'POST') {
      const changed = srv.checkOutcome === 'changed';
      return J({ ok: true, server_name: body.server_name, url: srv.saved ? srv.saved.url : '', status: srv.checkOutcome, checks: changed ? {} : { initialize: true, tools_list: true },
        diff: changed ? [{ what: 'new tool', detail: 'delete_everything' }, { what: 'tool changed', detail: 'read_file: its description or inputs are different' }] : [],
        review_needed: changed, checked_at: '2026-10-06T00:00:00Z', observed: sha('observed'), note: null, server: { name: 'fake-mcp', version: '2.1' },
        protocol_version: '2025-06-18', capabilities: ['tools'], tools: ['read_file', 'list_dir'].concat(changed ? ['delete_everything'] : []), tool_count: changed ? 3 : 2, truncated: false,
        purpose_verified: false, meaning: 'The server answered, and the tools it offers are recorded. This does not show that it does what you want it for.' });
    }
    if (path === '/api/desk/connect/custom/remote/adopt' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ ok: true, server_name: body.server_name, status: 'adopted', checks: { initialize: true, tools_list: true }, diff: [], review_needed: false, observed: body.observed,
        server: { name: 'fake-mcp', version: '2.1' }, protocol_version: '2025-06-18', capabilities: ['tools'], tools: ['read_file', 'list_dir', 'delete_everything'], tool_count: 3, truncated: false, purpose_verified: false });
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

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const P = (srv, suffix) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/desk/connect/' + suffix);
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|custom\/remote\/review)$/.test(r.path));
const shot = async (page, name, w) => { await page.evaluate(() => { const c = document.querySelector('[data-cr-card], [data-cr-check], [data-cr-result]'); if (c) c.scrollIntoView({ block: 'start' }); }); return page.screenshot({ path: resolve(SHOT_DIR, `connect_remote_${name}_${w}.png`) }); };

async function fits(page, label, sel = '[data-cr-save]') {
  const m = await page.evaluate((s) => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    const sv = document.querySelector(s);
    if (sv) sv.scrollIntoView({ block: 'center' });
    const r = sv ? sv.getBoundingClientRect() : null;
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
      btn: r ? { left: r.left, right: r.right } : null, vw: window.innerWidth };
  }, sel);
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  if (m.btn) check(m.btn.left >= 0 && m.btn.right <= m.vw, `${label}: the button is inside the ${m.vw}px window`, `${label}: button outside the window ${JSON.stringify(m.btn)}`);
}

async function openRemote(page) {
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  await page.fill('[data-cf-url]', 'https://tools.example.com');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.waitForSelector('[data-cr="closed"]', { timeout: 4000 });
  await page.click('[data-cr-open]');
  await page.waitForSelector('[data-cr="open"] [data-cr-url]');
}
const passcodePrompt = async (page, code) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', code);
  await page.click('.modal-content .btn-add');
};

async function projectScenario(browser, width, height) {
  console.log(`Project reach at ${width}px: Review, approve, Save, check`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openRemote(page);
  check(await page.isChecked('[data-cr-scope-opt][value="project"]') && !(await page.isChecked('[data-cr-scope-opt][value="global"]')), 'the reach defaults to one project; global is not chosen', 'default scope is not project');
  check(await page.$eval('[data-cr-review]', (b) => b.disabled), 'Review is off until an address is typed', 'Review enabled with no address');
  await page.fill('[data-cr-url]', 'https://mcp.example.com/mcp');
  await page.check('[data-cr-auth-opt][value="header"]');
  await page.fill('[data-cr-cred-vault="0"]', 'example.token');
  const projectId = await page.$eval('[data-cr-project]', (s) => s.value);
  check(!!projectId, `a project is chosen on the form (${projectId})`, 'no project chosen');
  check(writes(srv).length === 0 && P(srv, 'custom/remote/review').length === 0, 'nothing is sent before Review', 'requests before Review');
  await page.click('[data-cr-review]');
  await page.waitForSelector('[data-cr-card]', { timeout: 4000 });
  const sent = P(srv, 'custom/remote/review')[0].body;
  check(sent.url === 'https://mcp.example.com/mcp' && sent.scope === 'project' && sent.project_id === projectId && sent.auth === 'header'
    && sent.credentials.length === 1 && sent.credentials[0].header === 'Authorization' && sent.credentials[0].vault === 'example.token',
    'Review sends the typed address, the project reach and the header and Secrets NAMES', 'review body: ' + JSON.stringify(sent));
  check(!JSON.stringify(sent).includes('"value"') && !('passcode' in sent), 'the Review carries no credential value and no passcode', 'a value or passcode in the Review');
  check((await page.textContent('[data-cr-address]')).includes('https://mcp.example.com/mcp'), 'the card shows the exact address', 'address missing');
  check((await page.textContent('[data-cr-recipient]')).includes('https://mcp.example.com') && /nothing else/.test(await page.textContent('[data-cr-recipient]')), 'the card names the one recipient', 'recipient line wrong');
  check((await page.textContent('[data-cr-protocol-line]')).includes('Streamable HTTP') && /chosen from the address/.test(await page.textContent('[data-cr-protocol-line]')), 'the protocol is shown as chosen from the address', 'protocol line wrong');
  const reach = await page.$eval('[data-cr-reach]', (e) => [e.dataset.crReach, e.textContent]);
  check(reach[0] === 'project' && /only/.test(reach[1]), 'the card names the reach: one project', 'reach: ' + reach);
  const risks = await page.$$eval('[data-cr-risk]', (e) => e.map((x) => x.dataset.crRisk));
  check(risks.includes('remote_server_can_change') && risks.includes('secrets_to_remote') && risks.includes('not_purpose_verified') && !risks.includes('global_reach'), `risk labels are shown (${risks.join()})`, 'risks: ' + risks);
  check((await page.textContent('[data-cr-credential]')).includes('example.token') && (await page.textContent('[data-cr-credential]')).includes('Authorization'), 'the credential shows as a Secrets name and a header', 'credential line wrong');
  check(await page.$eval('[data-cr-contact]', (e) => e.dataset.crContact === 'none' && /Nothing has been sent/.test(e.textContent)), 'the card says nothing has been sent to the server', 'contact line wrong');
  check(await page.$eval('[data-cr-verification]', (e) => e.dataset.crVerification === 'not_verified'), 'the card does not claim purpose verification', 'verification claimed');
  check(await page.$eval('[data-cr-save]', (b) => b.disabled), 'Save is off until the card is approved', 'Save enabled before approval');
  check(writes(srv).length === 0, 'nothing has been written or contacted (only inspect and review were called)', 'writes so far: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await page.check('[data-cr-approve]');
  check(!(await page.$eval('[data-cr-save]', (b) => b.disabled)), 'ticking the approval turns Save on', 'Save still off');
  await shot(page, 'card', width);
  await fits(page, 'approval card');

  await page.click('[data-cr-save]');
  await passcodePrompt(page, 'wrong-passcode');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  await page.click('.modal-content .btn-secondary');
  check(P(srv, 'custom/commit').length === 1 && (await page.$$('[data-cr-card]')).length === 1, 'a wrong passcode reaches the server once and the card stays', 'commits ' + P(srv, 'custom/commit').length);
  await page.click('[data-cr-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cr-result="registered"]', { timeout: 6000 });
  const body = P(srv, 'custom/commit')[1].body;
  check(Object.keys(body).sort().join() === 'fingerprint,passcode,request_id', 'the Save carries only the request id, the fingerprint and the passcode', 'commit body keys: ' + Object.keys(body));
  const text = await page.textContent('[data-cr-result]');
  check(/Registered/.test(text) && !/Connected/.test(text) && /Saving does not contact the server/.test(text), 'the result says Registered, never Connected, and that Saving contacts nothing', 'result text: ' + text);
  check(P(srv, 'custom/remote/check').length === 0, 'the server has not been checked: the person did not ask', 'a check ran by itself');

  await page.click('[data-cr-check-run]');
  await page.waitForSelector('[data-cr-check="baseline_recorded"]', { timeout: 4000 });
  const checkBody = P(srv, 'custom/remote/check')[0].body;
  check(checkBody.server_name === 'mcp-example-com' && checkBody.scope === 'project' && checkBody.project_id === projectId && Object.keys(checkBody).length === 3,
    'the check names only the saved server and its reach', 'check body: ' + JSON.stringify(checkBody));
  const seen = await page.textContent('[data-cr-check]');
  check(/fake-mcp/.test(seen) && /read_file/.test(seen) && !/verified|Verified|Connected/.test(seen.replace(/does not show that it does what you want/, '')), 'the check shows what the server answered and never says verified or Connected', 'check text: ' + seen);
  check(/does not show that it does what you want/.test(await page.textContent('[data-cr-meaning]')), 'a handshake alone is not called purpose verification', 'meaning line missing');
  await shot(page, 'checked', width);
  await fits(page, 'checked result', '[data-cr-done]');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function changedScenario(browser, width, height) {
  console.log(`Changed server at ${width}px: the diff, the passcode-gated accept`);
  const srv = makeServer({ checkOutcome: 'changed' });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openRemote(page);
  await page.fill('[data-cr-url]', 'https://mcp.example.com/sse');
  await page.click('[data-cr-review]');
  await page.waitForSelector('[data-cr-card]');
  check((await page.textContent('[data-cr-protocol-line]')).includes('SSE') && P(srv, 'custom/remote/review')[0].body.protocol === undefined, 'an address ending in /sse is proposed as SSE and the form sent no protocol', 'protocol line: ' + (await page.textContent('[data-cr-protocol-line]')));
  await page.check('[data-cr-approve]');
  await page.click('[data-cr-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cr-result="registered"]');
  await page.click('[data-cr-check-run]');
  await page.waitForSelector('[data-cr-check="changed"]', { timeout: 4000 });
  const items = await page.$$eval('[data-cr-diff-item]', (e) => e.map((x) => x.dataset.crDiffItem));
  check(items.includes('new tool') && items.includes('tool changed'), `the diff names what moved (${items.join()})`, 'diff items: ' + items);
  check(/cleared/.test(await page.textContent('[data-cr-check="changed"]')) && (await page.textContent('[data-cr-checks]')).trim() === 'None', 'a changed server shows its earlier check as cleared', 'checks not cleared');
  check(P(srv, 'custom/remote/adopt').length === 0, 'nothing is accepted until the person asks', 'adopt ran by itself');
  await shot(page, 'changed', width);
  await fits(page, 'changed result', '[data-cr-adopt]');
  await page.click('[data-cr-adopt]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cr-check="adopted"]', { timeout: 4000 });
  const adopt = P(srv, 'custom/remote/adopt')[0].body;
  check(Object.keys(adopt).sort().join() === 'observed,passcode,scope,server_name' || Object.keys(adopt).sort().join() === 'observed,passcode,project_id,scope,server_name',
    'the accept names the server and the observation shown, with the passcode', 'adopt body keys: ' + Object.keys(adopt));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function exposureScenario(browser, width, height) {
  console.log(`Exposure at ${width}px: http:// and a local address each need their own tick`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openRemote(page);
  await page.fill('[data-cr-url]', 'http://localhost:8080/mcp');
  await page.check('[data-cr-scope-opt][value="global"]');
  await page.click('[data-cr-review]');
  await page.waitForSelector('[data-cr-card]');
  const required = await page.$$eval('[data-cr-exposure]', (e) => e.map((x) => x.dataset.crExposure));
  check(required.join() === 'unencrypted_connection,local_or_private_target', `the card names both exposures (${required.join()})`, 'exposures: ' + required);
  check(await page.$eval('[data-cr-approve]', (b) => b.disabled) && (await page.$$('[data-cr-needs-ack]')).length === 1, 'approval stays off until each exposure is ticked', 'approval enabled early');
  check(await page.$eval('[data-cr-reach]', (e) => e.dataset.crReach) === 'global' && (await page.$$('[data-cr-risk="global_reach"]')).length === 1, 'global reach is explicit and carries its risk label', 'global reach missing');
  await shot(page, 'exposure', width);
  await fits(page, 'exposure card', '[data-cr-approve]');
  await page.check('[data-cr-ack="unencrypted_connection"]');
  await page.waitForSelector('[data-cr-card]');
  check(await page.$eval('[data-cr-approve]', (b) => b.disabled), 'one tick of two is not enough', 'approval enabled after one tick');
  await page.check('[data-cr-ack="local_or_private_target"]');
  await page.waitForSelector('[data-cr-card]');
  check(!(await page.$eval('[data-cr-approve]', (b) => b.disabled)), 'both ticks turn approval on', 'approval still off after both ticks');
  const last = P(srv, 'custom/remote/review').at(-1).body;
  check(last.acknowledge.sort().join() === 'local_or_private_target,unencrypted_connection', 'each tick re-reviews with the exposure acknowledged, so the approval covers it', 'last review body: ' + JSON.stringify(last));
  check(P(srv, 'custom/commit').length === 0, 'no Save happened', 'a Save ran');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function editScenario(browser, width, height) {
  console.log(`Edits at ${width}px: a changed form is a new Review`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openRemote(page);
  await page.fill('[data-cr-url]', 'https://mcp.example.com/mcp');
  await page.click('[data-cr-review]');
  await page.waitForSelector('[data-cr-card]');
  await page.check('[data-cr-approve]');
  const first = await page.getAttribute('[data-cr-card]', 'data-cr-card');
  await page.fill('[data-cr-url]', 'https://other.example.com/mcp');
  check((await page.$$('[data-cr-card]')).length === 0, 'editing the address drops the card and its approval', 'the card survived an edit');
  await page.click('[data-cr-review]');
  await page.waitForSelector('[data-cr-card]');
  check(!(await page.isChecked('[data-cr-approve]')) && (await page.getAttribute('[data-cr-card]', 'data-cr-card')) !== first, 'the new card has a new fingerprint and starts unapproved', 'approval carried over');
  check(P(srv, 'custom/remote/review').length === 2 && P(srv, 'custom/commit').length === 0, 'two Reviews, no Save', 'reviews ' + P(srv, 'custom/remote/review').length);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await projectScenario(browser, w, h);
    await changedScenario(browser, w, h);
    await exposureScenario(browser, w, h);
    await editScenario(browser, w, h);
  }
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
