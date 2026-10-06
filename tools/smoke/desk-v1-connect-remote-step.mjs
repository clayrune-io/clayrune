#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's REMOTE MCP screens (MC-1062 ticket 11,
 * docs/desk_v1/connect_flow_tickets/11-remote-screen.md; static/js/desk-v1-connect-remote-step.js), the browser
 * half, against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection screens and the
 * Permissions screen are the real ones. The server half (exact fingerprint, same-origin recipient, redirect refusal,
 * passcode, the check and adopt routes) stays pinned by tests/test_desk_connect_remote*.py; the card here has the keys
 * `remote_mcp_service._card` builds.
 *
 *   1. Order        address, sign-in, credentials, Permissions, facts, (exposures), approve; the wizard sends nothing to the
 *                   server's address and nothing but the read-only review until Save.
 *   2. Protocols    Streamable HTTP and SSE both go from address to Registered; the proposed protocol is editable under Details.
 *   3. Token        a header NAME and a Secrets NAME are sent, never a value; Continue is off until both are named.
 *   4. Review       exact address, recipient, protocol, credential recipient, reach and the can-change risk on the first page;
 *                   the launch line and what a check shows are under the one Details.
 *   5. Exposures    http:// and a local address each need a box, OFF, four to a page; a tick reviews again; an approval and a
 *                   tick never carry to a changed card or a changed address.
 *   6. OAuth        marked "Save only"; issuer required; Saved, cannot run yet; no check is offered or sent.
 *   7. Save         off until approved; only {request_id, fingerprint, passcode}; a cancelled prompt sends nothing; a stale card
 *                   is dropped with the server's words.
 *   8. Check        starts only when asked; says Reached, never Verified; a changed server shows its diff and the accept asks
 *                   for the passcode.
 *   9. Fit          no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: docs/desk_v1/screens/connect_remote_step_*_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-remote-step.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
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

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
got = resolve.resolve('x.com', own_hosts=('mc.smoke.test',))
svc = got.pop('service') or registry.lookup(got['host'])
print(json.dumps({'x.com': {**got, **type_view.project_service(svc['id'] if svc else None)}}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

function makeServer({ commitState = 'registered', checkOutcome = 'baseline_recorded' } = {}) {
  const fx = loadFixtures();
  const srv = { log: [], fx, n: 0, cards: new Map(), commitState, checkOutcome, expire: false, forceRequired: null, previous: null, saved: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  srv.card = (b) => {
    const url = b.url.replace(/\/+$/, '') || b.url;
    const u = new URL(url);
    const plain = u.protocol === 'http:';
    const local = ['localhost', '127.0.0.1'].includes(u.hostname);
    const required = srv.forceRequired || [].concat(plain ? ['unencrypted_connection'] : []).concat(local ? ['local_or_private_target'] : []);
    const acknowledged = (b.acknowledge || []).filter((f) => required.includes(f));
    const missing = required.filter((f) => !acknowledged.includes(f));
    const protocol = b.protocol || (u.pathname.endsWith('/sse') ? 'sse' : 'streamable_http');
    const name = b.server_name || u.hostname.replace(/\./g, '-');
    const project = b.scope === 'project' ? fx.projects.find((p) => p.id === b.project_id) : null;
    const fingerprint = sha(JSON.stringify({ url, protocol, name, scope: b.scope, project: b.project_id, auth: b.auth, issuer: b.issuer, scopes: b.scopes, creds: b.credentials || [], ack: acknowledged }));
    const recipient = `${u.protocol}//${u.host}`;
    const creds = (b.credentials || []).map((c) => ({ header: c.header, vault: c.vault, prefix: c.prefix || '', env: 'MC_REMOTE_HEADER_0', placement: `HTTP header ${c.header}`, recipient }));
    const previous = srv.previous || null;
    const changes = previous && previous.scope !== b.scope ? [{ field: 'reach', from: previous.scope, to: b.scope }] : [];
    const oauth = b.auth === 'oauth';
    return {
      schema: 'desk-custom-card/1', kind: 'remote', request_id: 'req-' + (++srv.n) + '-abcdefghijklmnop', fingerprint,
      origin: { code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' },
      title: recipient, server_name: name, protocol, protocol_proposed: { value: u.pathname.endsWith('/sse') ? 'sse' : 'streamable_http', basis: u.pathname.endsWith('/sse') ? 'the address ends in /sse' : 'the address does not end in /sse' },
      url, recipient, auth: oauth ? { type: 'oauth', issuer: b.issuer, scopes: b.scopes || [] } : { type: b.auth === 'header' ? 'header' : 'none' },
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
      limitations: oauth ? [{ code: 'oauth_pending', message: 'OAuth is recorded only. This version cannot start the sign-in, so the server is saved but cannot run.' }] : [],
      changes, reask: changes.length > 0, replaces: changes.length ? sha('old') : null, approved: false,
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
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/static/js/desk-v1-connect-api-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its own Setup screen would shadow this smoke's fixture
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
    if (path.startsWith('/api/secrets') || path.startsWith('/api/browser')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
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
    if (path === '/api/desk/connect/custom/remote/review' && method === 'POST') {
      if (!body.url) return J({ error: 'enter the address of the server', code: 'bad_url' }, 400);
      const card = srv.card(body);
      srv.cards.set(card.request_id, card);
      return J(card);
    }
    if (path === '/api/desk/connect/custom/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const card = srv.cards.get(body.request_id);
      if (srv.expire || !card || card.fingerprint !== body.fingerprint) return J({ error: 'the configuration changed since you reviewed it. Review it again.', code: 'changed_since_review' }, 409);
      srv.saved = card;
      const oauth = card.auth.type === 'oauth';
      const st = oauth ? 'pending_runtime' : srv.commitState;
      const msg = { registered: 'Registered. It starts the first time an agent session uses it.', pending_runtime: oauth ? 'Saved and approved. OAuth is recorded only. This version cannot start the sign-in, so the server is saved but cannot run.' : 'Saved. This install has no secret wrapper, so nothing is registered yet.', setup_failed: 'Saved, but setup failed: the remote bridge could not be registered.' }[st];
      return J({ ok: true, approved: true, duplicate: false, fingerprint: card.fingerprint, server_name: card.server_name, scope: card.scope_chosen,
        project_id: card.reach.project ? card.reach.project.id : null, state: st, code: oauth ? 'oauth_pending' : '', message: msg }, 201);
    }
    if (path === '/api/desk/connect/custom/remote/check' && method === 'POST') {
      if (srv.saved && srv.saved.auth.type === 'oauth') return J({ error: 'This server signs in with OAuth, which this version does not start, so there is no credential to check with.', code: 'oauth_pending' }, 409);
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
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors, srv };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_remote_step_${name}_${w}.png`) });
const reqs = (srv, suffix) => srv.log.filter((r) => r.path === '/api/desk/connect/' + suffix);
const reviews = (srv) => reqs(srv, 'custom/remote/review');
const commits = (srv) => reqs(srv, 'custom/commit');
const checks = (srv) => reqs(srv, 'custom/remote/check');
const adopts = (srv) => reqs(srv, 'custom/remote/adopt');
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(types|custom\/remote\/review)$/.test(r.path));
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const has = (page, sel) => page.$(sel).then(Boolean);
const disabled = (page, sel) => page.$eval(sel, (b) => b.disabled);
const screenId = (page) => page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen);
const waitScreen = (page, id) => page.waitForFunction((i) => { const r = document.querySelector('[data-cfw]'); return !!r && r.dataset.cfwScreen === i; }, id, { timeout: 4000 });
const waitTitle = (page, t) => page.waitForFunction((x) => { const e = document.querySelector('[data-cfw-title]'); return !!e && e.textContent === x; }, t, { timeout: 4000 });
const waitStep = (page, s) => page.waitForSelector(`[data-cfw][data-cfw-step="${s}"]`, { timeout: 4000 });
const passcodePrompt = async (page, code) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', code);
  await page.click('.modal-content .btn-add');
};
const clickPrimary = (page) => page.click('[data-cfw-primary]');
const openDetails = async (page) => { if (!(await page.$eval('[data-cfw-details]', (d) => d.open))) await page.click('[data-cfw-details] > summary'); };
const waitCard = (page) => page.waitForSelector('[data-rs-address]', { timeout: 4000 });

async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    return { words, alts: root.querySelectorAll('[data-cfw-option], [data-cfw-alt]').length, details: root.querySelectorAll('details').length,
      nested: root.querySelectorAll('details details, form form').length, forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length };
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

// Open the wizard on x.com, take the API way to reach a fixture Setup screen, and switch the branch to the remote MCP.
// (The Package/Server-address choice that sets this variant is not this ticket's: the smoke sets it through the frame's own `select`.)
async function toRemoteSetup(page) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await page.evaluate(() => {
    window.DeskV1ConnectWizard.registerScreen({ id: 'fx-setup', step: 'setup', match: (sel) => sel.type === 'api', title: () => 'Fixture setup', copy: () => 'x', body: () => '<p>Setup</p>', bind: (root, api) => { window.__fxApi = api; }, primary: () => ({ label: 'Continue' }) });
  });
  await page.fill('[data-cfw-input]', 'x.com');
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check('[data-cfw-option="api"] input');
  await clickPrimary(page);
  await waitScreen(page, 'fx-setup');
  await page.evaluate(() => { window.__fxApi.select({ type: 'mcp', variant: 'custom-remote' }); window.__fxApi.go('setup'); });
  await waitScreen(page, 'remote-setup');
}

// Setup: address, sign-in (none | header | oauth), credentials; then Permissions (the first project); then the first Review page.
async function toReview(page, { url = 'https://tools.example.com/mcp', auth = 'none', protocol = '', vault = 'tools.token', issuer = 'https://auth.example.com', global = false } = {}) {
  await toRemoteSetup(page);
  await page.fill('[data-rs-url]', url);
  if (protocol) { await openDetails(page); await page.selectOption('[data-rs-protocol]', protocol); }
  await clickPrimary(page);
  await waitTitle(page, 'Server sign-in');
  await page.check(`[data-cfw-option="${auth}"] input`);
  await clickPrimary(page);
  if (auth === 'header') {
    await waitTitle(page, 'Server credentials');
    await page.fill('[data-rs-cred-vault="0"]', vault);
    await clickPrimary(page);
  } else if (auth === 'oauth') {
    await waitTitle(page, 'Server credentials');
    await page.fill('[data-rs-issuer]', issuer);
    await page.fill('[data-rs-scopes]', 'read write');
    await clickPrimary(page);
  }
  await waitStep(page, 'permissions');
  let id = null;
  if (global) await page.check('[data-cfp-scope][value="global"]');
  else { id = await page.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value); await page.selectOption('[data-cfp-project]', id); }
  await clickPrimary(page);
  await waitScreen(page, 'remote-review');
  await waitCard(page);
  return id;
}
async function toApprove(page, exposures = false) {
  await clickPrimary(page);
  if (exposures) { await waitTitle(page, 'Connection risks'); await clickPrimary(page); }
  await waitTitle(page, 'Approve connection');
}
async function save(page) {
  await page.check('[data-rs-approve]');
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await waitScreen(page, 'remote-result');
}

async function mainScenario(browser, width, height) {
  console.log(`\n== ${width}px: address, token, permissions, review, save, check ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toRemoteSetup(page);

  // 1: the first screen is the address and nothing else
  check((await text(page, '[data-cfw-title]')) === 'MCP server address' && (await screenId(page)) === 'remote-setup', 'Setup opens on "MCP server address"', 'setup title: ' + await text(page, '[data-cfw-title]'));
  check(!(await has(page, '[data-rs-auth], [data-rs-cred], [data-rs-issuer], [data-cfp-reach]')), 'no sign-in, credential or reach control on the first screen', 'a later control showed on the first screen');
  check(await disabled(page, '[data-cfw-primary]'), 'Continue is off until an address is typed', 'Continue on with no address');
  check((await text(page, '[data-cfw-stepof]')) === 'Setup · 1 of 2', 'the step counter reads "Setup · 1 of 2" while there is no sign-in', await text(page, '[data-cfw-stepof]'));
  check(await has(page, '[data-cfw-details] [data-rs-protocol]') && await has(page, '[data-cfw-details] [data-rs-name]') && !(await has(page, '[data-cfw-body] [data-rs-protocol]')), 'the protocol and the server name are under Details, not in the body', 'protocol/name placement');
  await rules(page, 'Address');
  await shot(page, 'address', width);
  await fits(page, 'Address');
  await page.fill('[data-rs-url]', 'tools.example.com');
  check(await disabled(page, '[data-cfw-primary]'), 'an address without https:// or http:// keeps Continue off', 'Continue on for a scheme-less address');
  await page.fill('[data-rs-url]', 'https://tools.example.com/mcp');
  check(!(await disabled(page, '[data-cfw-primary]')), 'a complete address turns Continue on', 'Continue still off');
  await clickPrimary(page);

  // 2: sign-in: three choices, OAuth marked Save only
  await waitTitle(page, 'Server sign-in');
  const opts = await page.$$eval('[data-cfw-option]', (e) => e.map((x) => x.dataset.cfwOption));
  check(opts.join() === 'none,header,oauth' && (await text(page, '[data-cfw-option="oauth"]')).includes('Save only'), 'sign-in offers None, a token from Secrets and OAuth marked "Save only"', 'options: ' + opts);
  check(await page.$eval('[data-cfw-option="none"] input', (i) => i.checked), 'No sign-in is the starting choice', 'default choice wrong');
  await rules(page, 'Sign-in');
  await shot(page, 'auth', width);
  await fits(page, 'Sign-in');
  await page.check('[data-cfw-option="header"] input');
  check((await text(page, '[data-cfw-stepof]')) === 'Setup · 2 of 3', 'choosing a token makes it "Setup · 2 of 3"', await text(page, '[data-cfw-stepof]'));
  await clickPrimary(page);

  // 3: token: header, prefix and a Secrets NAME
  await waitTitle(page, 'Server credentials');
  check((await page.$eval('[data-rs-cred-header="0"]', (i) => i.value)) === 'Authorization' && (await page.$eval('[data-rs-cred-prefix="0"]', (i) => i.value)) === 'Bearer ', 'the token row starts as Authorization with a Bearer prefix', 'default row wrong');
  check(await disabled(page, '[data-cfw-primary]'), 'Continue is off until the Secrets entry is named', 'Continue on with no Secrets entry');
  await page.fill('[data-rs-cred-vault="0"]', 'tools.token');
  check(!(await disabled(page, '[data-cfw-primary]')), 'naming the Secrets entry turns Continue on', 'Continue still off');
  check(/never typed here/.test(await text(page, '[data-rs-creds]')), 'the page says the token itself is never typed here', await text(page, '[data-rs-creds]'));
  await rules(page, 'Credentials');
  await shot(page, 'creds', width);
  await fits(page, 'Credentials');
  await clickPrimary(page);

  // Permissions is the real screen, third; nothing has been reviewed or sent
  await waitStep(page, 'permissions');
  check((await screenId(page)) === 'permissions-step' && (await has(page, '[data-cfp-reach]')) && !(await has(page, '[data-cfp-box]')), 'Permissions comes after the credentials and shows the reach only', 'permissions screen: ' + await screenId(page));
  check(reviews(srv).length === 0 && writes(srv).length === 0, 'nothing has been reviewed or written before the Review step', 'requests: ' + JSON.stringify(srv.log.map((r) => r.path)));
  const projectId = await page.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value);
  await page.selectOption('[data-cfp-project]', projectId);
  await clickPrimary(page);
  await waitScreen(page, 'remote-review');
  await waitCard(page);

  // 4: the review facts
  const sent = reviews(srv)[0].body;
  check(reviews(srv).length === 1 && sent.url === 'https://tools.example.com/mcp' && sent.scope === 'project' && sent.project_id === projectId && sent.auth === 'header'
    && sent.credentials.length === 1 && sent.credentials[0].header === 'Authorization' && sent.credentials[0].vault === 'tools.token' && sent.credentials[0].prefix === 'Bearer '
    && !('protocol' in sent) && !('acknowledge' in sent) && !('issuer' in sent),
    'Review sends the address, the project, the header NAME and the Secrets NAME, and no protocol, issuer or acknowledgement', 'review body: ' + JSON.stringify(sent));
  check(!('passcode' in sent) && !JSON.stringify(sent).includes('"value"'), 'the Review carries no passcode and no credential value', 'review body: ' + JSON.stringify(sent));
  check(checks(srv).length === 0 && commits(srv).length === 0 && writes(srv).length === 0, 'the Review reached no server: no check, no Save, no write', 'requests: ' + JSON.stringify(srv.log.map((r) => r.path)));
  check((await text(page, '[data-cfw-title]')) === 'Review server' && (await text(page, '[data-cfw-stepof]')) === 'Review · 1 of 2', 'the first Review page is "Review server", 1 of 2', await text(page, '[data-cfw-stepof]'));
  check((await text(page, '[data-rs-address]')).includes('https://tools.example.com/mcp') && (await text(page, '[data-rs-recipient] .desk-v1-cfw-fact-text')) === 'https://tools.example.com, and nothing else', 'the exact address and its recipient are on the first page', await text(page, '[data-rs-recipient]'));
  const proto = await text(page, '[data-rs-protocol-line]');
  check(/Streamable HTTP/.test(proto) && /chosen from the address: the address does not end in \/sse/.test(proto), 'the proposed protocol and why it was proposed are shown', proto);
  const cred = await text(page, '[data-rs-credentials]');
  check(cred.includes('tools.token') && cred.includes('Authorization') && cred.includes('https://tools.example.com') && /only/.test(cred), 'the credential shows as a Secrets entry name, its header and its one recipient', cred);
  const reach = await page.$eval('[data-rs-reach]', (e) => [e.dataset.rsReach, e.textContent]);
  check(reach[0] === 'project' && /only/.test(reach[1]), 'the reach is on the first page: one project', 'reach: ' + reach);
  check((await text(page, '[data-rs-contact]')).includes('Nothing has been sent to this server'), 'the page says nothing has been sent to the server', await text(page, '[data-rs-contact]'));
  check((await text(page, '[data-rs-origin]')).includes('User supplied; not reviewed by Clayrune'), 'the unreviewed notice is on the first page', 'origin label missing');
  const risks = await page.$$eval('[data-rs-risk]', (e) => e.map((x) => x.dataset.rsRisk));
  check(['user_supplied', 'remote_server_can_change', 'secrets_to_remote', 'not_purpose_verified'].every((c) => risks.includes(c)) && !risks.includes('global_reach'), `every material risk label is shown, including the can-change one (${risks.join()})`, 'risks: ' + risks);
  check(!(await has(page, '[data-cfw-body] [data-rs-command]')) && !(await has(page, '[data-cfw-body] [data-rs-verification]')), 'the launch line and what a check shows are not in the body', 'details content leaked into the body');
  await rules(page, 'Review server');
  await shot(page, 'review', width);
  await fits(page, 'Review server');
  const closed = await page.$eval('[data-cfw-details]', (d) => !d.open);
  await openDetails(page);
  const argv = await page.$$eval('[data-rs-command] li', (e) => e.map((x) => x.textContent));
  check(closed && argv.join(' ').includes('remote-mcp-bridge.py') && argv.includes('--protocol') && argv.includes('streamable_http') && argv.includes('https://tools.example.com/mcp'), 'the launch line is under the one Details, closed until opened', 'details: ' + argv.join(' '));
  check(/never counts as verifying a purpose/.test(await text(page, '[data-rs-verification]')), 'Details says what a check would and would not show', await text(page, '[data-rs-verification]'));
  await shot(page, 'review_details', width);
  await fits(page, 'Review details');

  // 5: approve and Save
  await clickPrimary(page);
  await waitTitle(page, 'Approve connection');
  check((await text(page, '[data-cfw-stepof]')) === 'Review · 2 of 2', 'the approval page reads "Review · 2 of 2" (no exposure page for an https address)', await text(page, '[data-cfw-stepof]'));
  check((await text(page, '[data-rs-sum="recipient"]')) === 'Requests go to https://tools.example.com and nothing else.' && /can change/.test(await text(page, '[data-rs-sum="can-change"]')), 'the approval page repeats the exact recipient and the can-change risk', await text(page, '[data-rs-summary]'));
  check((await text(page, '[data-cfw-primary]')) === 'Save' && await disabled(page, '[data-cfw-primary]'), 'Save is off until the approval is ticked', 'Save enabled before approval');
  check(writes(srv).length === 0, 'nothing has been written yet', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await rules(page, 'Approve');
  await shot(page, 'approve', width);
  await fits(page, 'Approve');
  await page.check('[data-rs-approve]');
  check(!(await disabled(page, '[data-cfw-primary]')), 'ticking the approval turns Save on', 'Save still off');
  await clickPrimary(page);
  await page.waitForSelector('input[id^="hp-passcode-"]');
  await page.click('.modal-content .btn-secondary');
  await page.waitForTimeout(150);
  check(commits(srv).length === 0 && (await screenId(page)) === 'remote-review', 'cancelling the passcode prompt sends nothing and keeps the card', 'commits ' + commits(srv).length);
  await clickPrimary(page);
  await passcodePrompt(page, 'wrong-passcode');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  await page.click('.modal-content .btn-secondary');
  check(commits(srv).length === 1 && (await screenId(page)) === 'remote-review' && (await page.$eval('[data-rs-approve]', (c) => c.checked)), 'a wrong passcode reaches the server once; the card and approval stay', 'commits ' + commits(srv).length);
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await waitScreen(page, 'remote-result');
  const cbody = commits(srv)[1].body;
  check(Object.keys(cbody).sort().join() === 'fingerprint,passcode,request_id' && cbody.request_id === [...srv.cards.keys()].pop(), 'the Save carries only the request id, the fingerprint and the passcode', 'commit: ' + JSON.stringify(Object.keys(cbody)));
  const rtext = await text(page, '[data-rs-result]');
  check((await text(page, '[data-cfw-title]')) === 'Registered' && /starts the first time/.test(rtext) && !/Connected|Verified/.test(rtext), 'the result says Registered and when it starts, never Connected or Verified', 'result: ' + rtext);
  check((await text(page, '[data-rs-saved]')).includes('https://tools.example.com') && !(await has(page, '[data-cfw-back]')), 'the result names the one recipient and has no Back', 'saved fact or Back wrong');
  check(checks(srv).length === 0, 'saving did not contact the server', 'a check ran at Save');
  await rules(page, 'Result');
  await shot(page, 'result', width);
  await fits(page, 'Result');

  // 6: the check is the person's to start
  await page.click('[data-rs-check-run]');
  await page.waitForSelector('[data-rs-check="baseline_recorded"]');
  const target = checks(srv)[0].body;
  check(checks(srv).length === 1 && Object.keys(target).sort().join() === 'project_id,scope,server_name' && target.scope === 'project' && target.project_id === projectId, 'the check names only the saved server and its reach, once', 'check body: ' + JSON.stringify(target));
  const word = await text(page, '[data-rs-check-word]');
  const all = await text(page, '[data-rs-check-slot]');
  check(/^Reached\./.test(word) && !/\bVerified\b/.test(all) && /does not show that it does what you want/.test(all), 'a check says Reached, never Verified, and that it does not show the purpose', 'check text: ' + all);
  check((await text(page, '[data-rs-observed-tools]')).includes('read_file') && !(await has(page, '[data-rs-adopt]')), 'it lists the tools the server offered and offers no accept when nothing changed', 'observed tools wrong');
  await shot(page, 'checked', width);
  await fits(page, 'Result after a check');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function protocolScenario(browser, width, height) {
  console.log(`\n== ${width}px: both protocols go from address to Registered ==`);
  // SSE, proposed from the address
  let h = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(h.page, { url: 'https://events.example.com/sse' });
  check(reviews(h.srv)[0].body.url === 'https://events.example.com/sse' && !('protocol' in reviews(h.srv)[0].body), 'an address ending /sse is sent without a protocol: the server proposes', JSON.stringify(reviews(h.srv)[0].body));
  const p1 = await text(h.page, '[data-rs-protocol-line]');
  check(/SSE/.test(p1) && /the address ends in \/sse/.test(p1), 'the card proposes SSE and says why', p1);
  await toApprove(h.page); await save(h.page);
  check((await text(h.page, '[data-cfw-title]')) === 'Registered', 'the SSE server reaches Registered', await text(h.page, '[data-cfw-title]'));
  await h.ctx.close();
  // Streamable HTTP, chosen under Details
  h = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(h.page, { url: 'https://events.example.com/sse', protocol: 'streamable_http' });
  check(reviews(h.srv)[0].body.protocol === 'streamable_http', 'the Details choice is sent: Streamable HTTP at an address that ends /sse', JSON.stringify(reviews(h.srv)[0].body));
  const p2 = await text(h.page, '[data-rs-protocol-line]');
  check(/Streamable HTTP/.test(p2) && !/chosen from the address/.test(p2), 'the card shows the protocol the person chose, not the proposal', p2);
  await toApprove(h.page); await save(h.page);
  check((await text(h.page, '[data-cfw-title]')) === 'Registered', 'the Streamable HTTP server reaches Registered', await text(h.page, '[data-cfw-title]'));
  // and SSE chosen explicitly
  const h2 = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(h2.page, { url: 'https://tools.example.com/mcp', protocol: 'sse' });
  check(reviews(h2.srv)[0].body.protocol === 'sse' && /SSE/.test(await text(h2.page, '[data-rs-protocol-line]')), 'choosing SSE under Details sends SSE and the card shows it', JSON.stringify(reviews(h2.srv)[0].body));
  check(realErrors(h.pageErrors).length === 0 && realErrors(h2.pageErrors).length === 0, 'no page errors', 'page errors');
  await h.ctx.close(); await h2.ctx.close();
}

async function exposureScenario(browser, width, height) {
  console.log(`\n== ${width}px: exposures are a page of their own, off, and never carried ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(page, { url: 'http://localhost:8080/mcp' });
  check(reviews(srv).length === 1 && !('acknowledge' in reviews(srv)[0].body), 'the first review carries no acknowledgement', JSON.stringify(reviews(srv)[0].body));
  const risks = await page.$$eval('[data-rs-risk]', (e) => e.map((x) => x.dataset.rsRisk));
  check(risks.includes('unencrypted_connection') && risks.includes('local_or_private_target'), 'both exposures are named on the first page too', 'risks: ' + risks);
  check((await text(page, '[data-cfw-stepof]')) === 'Review · 1 of 3', 'an exposed address adds a page: "Review · 1 of 3"', await text(page, '[data-cfw-stepof]'));
  await clickPrimary(page);
  await waitTitle(page, 'Connection risks');
  const boxes = await page.$$eval('[data-rs-ack]', (e) => e.map((x) => [x.dataset.rsAck, x.checked]));
  check(boxes.length === 2 && boxes.every(([, c]) => !c), 'each exposure has its own box and every box starts OFF', 'boxes: ' + JSON.stringify(boxes));
  check(await disabled(page, '[data-cfw-primary]'), 'Continue is off until each exposure is ticked', 'Continue on with exposures unticked');
  await rules(page, 'Connection risks');
  await shot(page, 'exposures', width);
  await fits(page, 'Connection risks');
  const n0 = reviews(srv).length;
  await page.check('[data-rs-ack="unencrypted_connection"]');
  await page.waitForFunction(() => { const b = document.querySelector('[data-rs-ack="unencrypted_connection"]'); return !!b && b.checked; }, null, { timeout: 4000 });
  check(reviews(srv).length === n0 + 1 && reviews(srv)[n0].body.acknowledge.join() === 'unencrypted_connection', 'a tick reviews again with exactly that exposure acknowledged', JSON.stringify(reviews(srv).map((r) => r.body.acknowledge)));
  check(await disabled(page, '[data-cfw-primary]'), 'one of two ticks keeps Continue off', 'Continue on after one tick');
  await page.check('[data-rs-ack="local_or_private_target"]');
  await page.waitForFunction(() => { const b = document.querySelector('[data-rs-ack="local_or_private_target"]'); return !!b && b.checked && !document.querySelector('[data-cfw-primary]').disabled; }, null, { timeout: 4000 });
  check(reviews(srv)[n0 + 1].body.acknowledge.sort().join() === 'local_or_private_target,unencrypted_connection', 'the second tick sends both', JSON.stringify(reviews(srv)[n0 + 1].body.acknowledge));
  await clickPrimary(page);
  await waitTitle(page, 'Approve connection');
  check(/^2 exposures you accepted/.test(await text(page, '[data-rs-sum="exposures"]')), 'the approval page counts the exposures accepted', await text(page, '[data-rs-summary]'));
  await page.check('[data-rs-approve]');
  check(!(await disabled(page, '[data-cfw-primary]')), 'Save is on with both ticks and the approval', 'Save off');

  // an untick after approving is a new card: the approval does not carry
  await page.click('[data-rs-rprev]');
  await waitTitle(page, 'Connection risks');
  const n1 = reviews(srv).length;
  await page.uncheck('[data-rs-ack="local_or_private_target"]');
  await page.waitForFunction(() => { const b = document.querySelector('[data-rs-ack="local_or_private_target"]'); return !!b && !b.checked; }, null, { timeout: 4000 });
  check(reviews(srv).length === n1 + 1 && reviews(srv)[n1].body.acknowledge.join() === 'unencrypted_connection', 'unticking reviews again with only the remaining exposure', JSON.stringify(reviews(srv)[n1].body));
  await page.check('[data-rs-ack="local_or_private_target"]');
  await page.waitForFunction(() => !document.querySelector('[data-cfw-primary]').disabled, null, { timeout: 4000 });
  await clickPrimary(page);
  await waitTitle(page, 'Approve connection');
  check(!(await page.$eval('[data-rs-approve]', (c) => c.checked)) && await disabled(page, '[data-cfw-primary]'), 'the earlier approval does not carry to the new card: the box is clear and Save is off', 'approval carried over: box=' + await page.$eval('[data-rs-approve]', (c) => c.checked) + ' saveDisabled=' + await disabled(page, '[data-cfw-primary]'));

  // a changed address: the ticks are gone
  await page.click('[data-cfw-back]');
  await waitStep(page, 'permissions');
  await page.click('[data-cfw-back]');
  await waitStep(page, 'setup');
  while (await has(page, '[data-rs-prev]')) { await page.click('[data-rs-prev]'); await page.waitForTimeout(60); }
  await page.fill('[data-rs-url]', 'http://localhost:9090/mcp');
  await clickPrimary(page); await waitTitle(page, 'Server sign-in');
  await clickPrimary(page); await waitStep(page, 'permissions');
  const n2 = reviews(srv).length;
  await clickPrimary(page); await waitScreen(page, 'remote-review'); await waitCard(page);
  check(reviews(srv).length === n2 + 1 && !('acknowledge' in reviews(srv)[n2].body) && reviews(srv)[n2].body.url === 'http://localhost:9090/mcp', 'a changed address reviews again with NO acknowledgement carried over', JSON.stringify(reviews(srv)[n2].body));
  await clickPrimary(page); await waitTitle(page, 'Connection risks');
  check(await page.$$eval('[data-rs-ack]', (e) => e.length === 2 && e.every((x) => !x.checked)) && await disabled(page, '[data-cfw-primary]'), 'the exposure boxes are OFF again for the new address', 'ticks carried to a changed address');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function pagedScenario(browser, width, height) {
  console.log(`\n== ${width}px: more than four exposure choices are paged, not hidden ==`);
  const srv = makeServer();
  srv.forceRequired = ['unencrypted_connection', 'local_or_private_target', 'extra_three', 'extra_four', 'extra_five', 'extra_six'];
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toReview(page, { url: 'http://localhost:8080/mcp' });
  await clickPrimary(page);
  await waitTitle(page, 'Connection risks');
  check((await page.$$('[data-rs-ack]')).length === 4 && (await has(page, '[data-cfw-pager]')) && /Page 1 of 2/.test(await text(page, '[data-cfw-pageof]')), 'six exposures show four to a page with a pager (Page 1 of 2)', 'paging wrong');
  await rules(page, 'Connection risks (paged)');
  await page.click('[data-cfw-page="next"]');
  await page.waitForSelector('[data-rs-exposure="extra_six"]');
  check((await page.$$('[data-rs-ack]')).length === 2, 'the next page holds the other two: none is concealed', 'second page wrong');
  await shot(page, 'exposures_paged', width);
  await fits(page, 'Connection risks page 2');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function oauthScenario(browser, width, height) {
  console.log(`\n== ${width}px: OAuth is Save only: saved, cannot run, no check ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toRemoteSetup(page);
  await page.fill('[data-rs-url]', 'https://tools.example.com/mcp');
  await clickPrimary(page); await waitTitle(page, 'Server sign-in');
  await page.check('[data-cfw-option="oauth"] input');
  await clickPrimary(page); await waitTitle(page, 'Server credentials');
  check(/Save only|cannot start the sign-in/.test(await text(page, '[data-rs-oauth]')) && /OAuth is recorded only/.test(await text(page, '[data-rs-oauth]')), 'the OAuth page says it is recorded only and cannot start the sign-in', await text(page, '[data-rs-oauth]'));
  check(await disabled(page, '[data-cfw-primary]') && !(await has(page, '[data-rs-cred-vault="0"]')), 'Continue is off until an issuer is named, and no token field is offered', 'oauth page wrong');
  await rules(page, 'OAuth');
  await shot(page, 'oauth', width);
  await fits(page, 'OAuth');
  await page.fill('[data-rs-issuer]', 'https://auth.example.com');
  await page.fill('[data-rs-scopes]', 'read, write');
  check(!(await disabled(page, '[data-cfw-primary]')), 'naming the issuer turns Continue on', 'Continue still off');
  await clickPrimary(page); await waitStep(page, 'permissions');
  const id = await page.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value);
  await page.selectOption('[data-cfp-project]', id);
  await clickPrimary(page); await waitScreen(page, 'remote-review'); await waitCard(page);
  const sent = reviews(srv)[0].body;
  check(sent.auth === 'oauth' && sent.issuer === 'https://auth.example.com' && sent.scopes.join() === 'read,write' && !('credentials' in sent), 'Review sends the issuer and the scopes as a list, and no credentials', JSON.stringify(sent));
  check(/Save only/.test(await text(page, '[data-rs-auth-line]')) && (await has(page, '[data-rs-limit="oauth_pending"]')), 'the review page says Save only and shows the server\'s own OAuth limitation', await text(page, '[data-rs-auth-line]'));
  await openDetails(page);
  const of = await text(page, '[data-rs-oauth-facts]');
  check(of.includes('https://auth.example.com') && of.includes('read') && of.includes('write'), 'Details shows the issuer and the scopes', of);
  await toApprove(page); await save(page);
  check((await text(page, '[data-cfw-title]')) === 'Saved, cannot run yet' && !/^Registered/.test(await text(page, '[data-rs-result]')), 'the result says "Saved, cannot run yet", never Registered', await text(page, '[data-rs-result]'));
  check(!(await has(page, '[data-rs-check-run]')) && /no check to run/.test(await text(page, '[data-rs-oauth-result]')), 'no check is offered, and the result says there is none to run', 'a check is offered for OAuth');
  check(checks(srv).length === 0, 'no check request was sent', 'check sent');
  await shot(page, 'oauth_result', width);
  await fits(page, 'OAuth result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function invalidateScenario(browser, width, height) {
  console.log(`\n== ${width}px: a changed request is a new review; a stale card is dropped ==`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toReview(page, { auth: 'header' });
  await toApprove(page);
  await page.check('[data-rs-approve]');

  // Back to Permissions and all projects: a different reach is a different card, and the earlier approval is gone
  await page.click('[data-cfw-back]');
  await waitStep(page, 'permissions');
  srv.previous = { scope: 'project' };
  await page.check('[data-cfp-scope][value="global"]');
  const n1 = reviews(srv).length;
  await clickPrimary(page);
  await waitScreen(page, 'remote-review');
  await waitCard(page);
  check(reviews(srv).length === n1 + 1 && reviews(srv)[n1].body.scope === 'global' && !('project_id' in reviews(srv)[n1].body), 'changing the reach reviews again, as global with no project', JSON.stringify(reviews(srv).map((r) => r.body.scope)));
  check((await text(page, '[data-rs-reach]')).includes('every project') && await has(page, '[data-rs-risk="global_reach"]'), 'the new card names every project and carries the global risk label', await text(page, '[data-rs-reach]'));
  check(await has(page, '[data-rs-reask]') && (await text(page, '[data-rs-change="reach"]')).includes('project') && (await text(page, '[data-rs-change="reach"]')).includes('global'), 'a server that was approved before says it needs a new approval and shows what changed', 'reask banner missing');
  await shot(page, 'global_reask', width);
  await fits(page, 'Review server (global, changed)');
  await toApprove(page);
  check(!(await page.$eval('[data-rs-approve]', (c) => c.checked)) && await disabled(page, '[data-cfw-primary]'), 'the approval from the earlier card does not carry: the box is clear and Save is off', 'approval carried over: box=' + await page.$eval('[data-rs-approve]', (c) => c.checked) + ' saveDisabled=' + await disabled(page, '[data-cfw-primary]'));

  // Back to Setup (credentials page): a changed header name is another card
  await page.click('[data-cfw-back]'); await waitStep(page, 'permissions');
  await page.click('[data-cfw-back]'); await waitStep(page, 'setup');
  check((await screenId(page)) === 'remote-setup' && (await has(page, '[data-rs-cred-header="0"]')), 'Back returns to the credentials page of the server setup', await screenId(page));
  await page.fill('[data-rs-cred-header="0"]', 'X-Api-Key');
  await page.fill('[data-rs-cred-prefix="0"]', '');
  const n2 = reviews(srv).length;
  await clickPrimary(page); await waitStep(page, 'permissions');
  await clickPrimary(page); await waitScreen(page, 'remote-review'); await waitCard(page);
  check(reviews(srv).length === n2 + 1 && reviews(srv)[n2].body.credentials[0].header === 'X-Api-Key' && !('prefix' in reviews(srv)[n2].body.credentials[0]), 'a changed header reviews again with the new header', 'reviews: ' + JSON.stringify(reviews(srv)[n2].body));
  check((await text(page, '[data-rs-credentials]')).includes('X-Api-Key'), 'the card shows the new header', await text(page, '[data-rs-credentials]'));

  // A stale card: the server no longer accepts it
  await toApprove(page);
  await page.check('[data-rs-approve]');
  srv.expire = true;
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-rs-review-error]', { timeout: 4000 });
  const msg = await text(page, '[data-cfw-body]');
  check(/changed since you reviewed it/.test(msg) && /Review again/.test(msg), 'a card the server no longer accepts is dropped with the server\'s own words and "Review again"', 'message: ' + msg);
  check((await screenId(page)) === 'remote-review' && !(await has(page, '[data-rs-approve]')), 'it offers a new review, not a way to save the old card', 'stale card is still savable');
  await shot(page, 'stale', width);
  srv.expire = false;
  const n3 = reviews(srv).length;
  await page.click('[data-rs-rereview]');
  await waitCard(page);
  check(reviews(srv).length === n3 + 1, '"Review again" asks the server for a new card', 'no new review');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function changedScenario(browser, width, height) {
  console.log(`\n== ${width}px: a changed server shows its diff; the accept asks for the passcode ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer({ checkOutcome: 'changed' }), width, height });
  await toReview(page);
  await toApprove(page); await save(page);
  await page.click('[data-rs-check-run]');
  await page.waitForSelector('[data-rs-check="changed"]');
  const word = await text(page, '[data-rs-check-word]');
  check(/^Reached, but it has changed/.test(word) && !/\bVerified\b/.test(word), 'a changed server says it was reached and has changed, not verified', word);
  const diff = await page.$$eval('[data-rs-diff-item]', (e) => e.map((x) => x.textContent));
  check(diff.length === 2 && diff[0].includes('delete_everything'), 'the diff shows the new tool and the changed tool', 'diff: ' + diff);
  check(await has(page, '[data-rs-adopt]') && adopts(srv).length === 0, 'an accept button is offered and nothing has been accepted', 'adopt wrong');
  await shot(page, 'changed', width);
  await fits(page, 'Changed server');
  await page.click('[data-rs-adopt]');
  await page.waitForSelector('input[id^="hp-passcode-"]');
  await page.click('.modal-content .btn-secondary');
  await page.waitForTimeout(150);
  check(adopts(srv).length === 0 && (await has(page, '[data-rs-adopt]')), 'cancelling the passcode prompt accepts nothing', 'adopts ' + adopts(srv).length);
  await page.click('[data-rs-adopt]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-rs-check="adopted"]');
  const ab = adopts(srv)[0].body;
  check(adopts(srv).length === 1 && Object.keys(ab).sort().join() === 'observed,passcode,project_id,scope,server_name', 'the accept sends the saved server, what was observed and the passcode', 'adopt: ' + JSON.stringify(Object.keys(ab)));
  check(/^Accepted as the new baseline/.test(await text(page, '[data-rs-check-word]')) && !(await has(page, '[data-rs-adopt]')), 'it then says the changes were accepted', await text(page, '[data-rs-check-word]'));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function stateScenario(browser, width, height) {
  console.log(`\n== ${width}px: result state setup_failed ==`);
  const { ctx, page, pageErrors } = await newPage(browser, { srv: makeServer({ commitState: 'setup_failed' }), width, height });
  await toReview(page);
  await toApprove(page); await save(page);
  const t = await text(page, '[data-rs-result]');
  check((await text(page, '[data-cfw-title]')) === 'Saved, setup failed' && t.startsWith('Saved, setup failed') && !/Registered|Connected/.test(t), 'the result says "Saved, setup failed" with the server\'s words', 'result: ' + t);
  check(!(await has(page, '[data-rs-check-run]')), 'a server that is not registered offers no check', 'check offered');
  await shot(page, 'state_setup_failed', width);
  await fits(page, 'Result setup_failed');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function bigTextScenario(browser, width, height) {
  console.log(`\n== ${width}px: 200% text ==`);
  const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(page, { auth: 'header' });
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await fits(page, 'Review server at 200% text');
  await openDetails(page);
  await fits(page, 'Review details at 200% text');
  await clickPrimary(page); await waitTitle(page, 'Approve connection');
  await fits(page, 'Approve at 200% text');
  await shot(page, 'approve_200pct', width);
  await ctx.close();
}

const browser = await chromium.launch({ headless: true });
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await mainScenario(browser, w, h);
    await protocolScenario(browser, w, h);
    await exposureScenario(browser, w, h);
    await pagedScenario(browser, w, h);
    await oauthScenario(browser, w, h);
    await invalidateScenario(browser, w, h);
    await changedScenario(browser, w, h);
    await stateScenario(browser, w, h);
    await bigTextScenario(browser, w, h);
  }
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
