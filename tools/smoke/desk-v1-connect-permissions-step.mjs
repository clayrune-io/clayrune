#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's PERMISSIONS screen (MC-1062 ticket 08,
 * docs/desk_v1/connect_flow_tickets/08-permissions-screen.md; static/js/desk-v1-connect-permissions-step.js), the
 * browser half, against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection/Setup
 * screens are the real ones, drawn from the REAL type projection (mc.desk_connect.type_view), computed once by Python.
 * The server halves (policy contract, enforcement) are pinned in tests/test_desk_connect_permission*.py.
 *
 *   1. Own step     no permission control on the Connection or Setup screens; the screen opens after Setup.
 *   2. Offers       only what the chosen route runs AND the profile covers: X API = Read your posts + Post; X sign-in =
 *                   Read (4 capabilities, through the browser) + the site row, no Post; LinkedIn sign-in = the site row only.
 *   3. Start OFF    a new account's rows start unchecked; Continue and the choices send nothing.
 *   4. Post w/o Read  the Desk-cannot-confirm notice sits next to the choices, appears and clears with the choice.
 *   5. Exact scopes the commit draft names exactly route/purpose/capability: Post is `publish/post` only (no Reply,
 *                   Article, media), Read is the route's covered capabilities only.
 *   6. Preserve     an existing account's grants show as they are, an untouched screen sends nothing, a change keeps
 *                   every row it did not touch (a second route's scope); legacy / invalid accounts say so.
 *   7. Four owners  Desk permission, Browser permission (profile, shared), saved login (vault, a fact), MCP reach.
 *   8. Paid API     the API Read row carries the cost notice; the browser Read does not.
 *   9. Passcodes    Desk and browser-site writes are two prompts; cancelling the first stops the second; a refusal is a
 *                   status with the server's words; a retry of the same draft reuses the request id.
 *  10. MCP          "Use this server's tools": reach only, NO Read/Post switch; project must be chosen; global is flagged.
 *  11. Reference    grants nothing and sends nothing.
 *  12. Fit          no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: _scratch/connect_permissions_step/*.png (not committed).
 *
 * RUN   cd tools/smoke && node desk-v1-connect-permissions-step.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connect_permissions_step');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

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

const PASSCODE = 'right-passcode';
const OAUTH = (purpose, capability) => ({ purpose, capability, route_id: 'x-oauth' });
const POST = OAUTH('publish', 'post');
const READ = OAUTH('read_own', 'own_posts');
const BROWSER_READ = { purpose: 'read_own', capability: 'own_posts', route_id: 'x-browser' };
const explicit = (id, scopes) => ({ state: 'explicit', version: 1, account_id: id, service: 'x', account_kind: 'account',
  read: scopes.some((s) => s.purpose === 'read_own'), post: scopes.some((s) => s.purpose === 'publish'), scopes, fingerprint: 'fp', updated_at: 'now' });

function makeServer() {
  const fx = loadFixtures();
  const srv = {
    log: [], fx, commitMode: 'ok',
    policies: { 'acct-1': explicit('acct-1', [READ, POST, BROWSER_READ]), 'acct-2': explicit('acct-2', [POST]),
      'acct-3': { state: 'legacy', read: null, post: null, scopes: [], fingerprint: null }, 'acct-4': { state: 'invalid', read: false, post: false, scopes: [], fingerprint: null } },
    profiles: ['x-main'], agentRead: { 'x-main': { enabled: true, domains: ['example.org'] } },
    secrets: [{ name: 'x.ron', username: 'ron', allow_unattended: true }, { name: 'x.quiet', username: 'q', allow_unattended: false }],
    logins: [{ name: 'x.ron', matches: true }, { name: 'x.quiet', matches: true }],
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
    if (path === '/static/js/desk-v1-connect-api-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its own Setup screen would shadow this smoke's fixture
    if (path === '/static/js/desk-v1-connect-unknown-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' }); // reference permissions would shadow this smoke's fixture
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
    if (path === '/api/secrets' && method === 'GET') { srv.log.push({ method, path, body }); return J({ secrets: srv.secrets }); }
    if (path.startsWith('/api/secrets') || path === '/api/browser/launch') { srv.log.push({ method, path, body }); return J({ error: 'not expected' }, 500); }
    if (path === '/api/browser/profiles') { srv.log.push({ method, path, body }); return J({ profiles: srv.profiles.map((name) => ({ name, size_mb: 1, last_used: null, in_use_by: null })) }); }
    if (path === '/api/browser/agent-read') { srv.log.push({ method, path, body }); return J({ profiles: srv.agentRead }); }
    const m = path.match(/^\/api\/browser\/profiles\/([^/]+)\/agent-read$/);
    if (m && method === 'PUT') {
      srv.log.push({ method, path, body });
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.agentRead[m[1]] = { enabled: !!body.enabled, domains: body.domains };
      return J({ ok: true, profile: m[1], enabled: !!body.enabled, domains: body.domains });
    }
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
    if (path === '/api/desk/connect/signin/options') return J({ service: body.service, routes: [], logins: srv.logins, vault_locked: false });
    if (path === '/api/desk/connect/purposes') return J({ service: body.service, accounts: [{ id: 'acct-1', label: 'ron', bound: [{ purpose: 'read_own', route_id: 'x-oauth', route_title: 'X API', capabilities: ['own_posts'] }, { purpose: 'publish', route_id: 'x-oauth', route_title: 'X API', capabilities: ['post', 'media'] }] }] });
    const pv = path.match(/^\/api\/desk\/connect\/permissions\/([^/]+)$/);
    if (pv && method === 'GET') return J({ account_id: pv[1], policy: srv.policies[pv[1]] || { state: 'explicit', read: false, post: false, scopes: [], fingerprint: 'fp' } });
    if (path === '/api/desk/connect/permissions/commit' && method === 'POST') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.commitMode === 'refuse') return J({ error: 'that route does not cover this exact capability', code: 'unsupported_scope' }, 400);
      const d = body.draft;
      srv.policies[d.account_id] = { ...explicit(d.account_id, d.scopes), account_kind: d.account_kind };
      return J({ ok: true, duplicate: false, account_id: d.account_id, policy: srv.policies[d.account_id] }, 201);
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
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !['/api/desk/connect/types', '/api/desk/connect/purposes', '/api/desk/connect/signin/options'].includes(r.path));
const repaint = (page) => page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));
const prompt = (page, code) => page.fill('input[id^="hp-passcode-"]', code).then(() => page.click('.modal-content .btn-add'));
const cancelPrompt = (page) => page.click('.modal-content .btn-secondary');
const waitPrompt = (page) => page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
const mode = async (page, m) => { await page.check(`[data-cfw-option="${m}"] input`); await page.waitForSelector(`[data-cfw-option="${m}"] input:checked`); };
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const has = (page, sel) => page.$(sel).then(Boolean);
const box = (page, id) => page.$eval(`[data-cfp-box="${id}"]`, (b) => b.checked);
const boxes = (page) => page.$$eval('[data-cfp-box]', (bs) => bs.map((b) => b.dataset.cfpBox));

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

// Fixture screens for the pieces that are other tickets: Setup for the API and MCP branches (no real screen yet),
// Review and Result. The Permissions screen under test is the REAL one.
async function registerFixtures(page) {
  await page.evaluate(() => {
    const W = window.DeskV1ConnectWizard;
    window.__fx = { api: null };
    W.registerScreen({
      id: 'fx-setup', step: 'setup', match: (sel) => sel.type === 'api' || sel.type === 'mcp',
      title: () => 'Fixture setup', copy: () => 'Setup is another ticket.', body: () => '<p>Setup</p>',
      bind: (root, api) => { window.__fx.api = api; }, primary: () => ({ label: 'Continue' }),
    });
    W.registerScreen({ id: 'fx-review', step: 'review', title: () => 'Fixture review', copy: () => 'Review.', body: () => '<p>Review</p>', bind: (root, api) => { window.__fx.api = api; }, primary: () => ({ label: 'Save' }) });
  });
}

// Open the wizard on `address`, pick `type`, and walk to Permissions. `target` goes to both the sign-in screen and the
// permissions screen (the unbuilt account screen will hand it over). `signin`: { login: false | 'x.ron', profile }.
async function toPermissions(page, { address = 'x.com', type, target, signin = {} }) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await registerFixtures(page);
  await page.fill('[data-cfw-input]', address);
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check(`[data-cfw-option="${type}"] input`);
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  if (target) await page.evaluate((t) => { window.DeskV1ConnectPermissionsStep.setTarget(t); if (window.DeskV1ConnectLoginStep) window.DeskV1ConnectLoginStep.setTarget(t); }, target);
  if (type === 'signin') {
    await repaint(page);
    await page.waitForSelector('[data-cfw-screen="login-step"]');
    if (signin.login) {
      await mode(page, 'saved');
      await page.waitForSelector('[data-lg-pick]');
      await page.selectOption('[data-lg-pick]', signin.login);
    } else {
      await mode(page, 'manual');
      await page.fill('[data-lg-newprofile]', signin.profile || 'x-ronx');
    }
  }
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'permissions');
  await page.waitForSelector('[data-cfw-screen="permissions-step"]');
  await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 4000 });
}
const NEW_X = { kind: 'account', identity: 'ronx', account: { new: { identity: 'ronx', label: '' } } };
const acct = (id, kind = 'account') => ({ kind, identity: 'ron', account: { id } });

// Run apply() while answering the passcode prompts in order: 'ok' | 'cancel' | 'wrong'.
async function applyWith(page, id, answers) {
  const done = page.evaluate((i) => window.DeskV1ConnectPermissionsStep.apply(i), id);
  for (const a of answers) {
    await waitPrompt(page);
    if (a === 'cancel') await cancelPrompt(page); else await prompt(page, a === 'ok' ? PASSCODE : 'nope');
    await page.waitForTimeout(80);
  }
  return done;
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1-5, 8, 9, 12: X API, a new account ────────────────────────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
    await page.click('[data-conn-add-tile]'); await page.waitForSelector('[data-cfw-input]');
    await registerFixtures(page);
    await page.fill('[data-cfw-input]', 'x.com'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
    check(!(await has(page, '[data-cfp-box], [data-cfp-reach]')) && !/permission/i.test(await text(page, '[data-cfw-body]')), 'the Connection screen carries no permission control or wording', 'a permission control showed on Connection');
    await page.check('[data-cfw-option="api"] input'); await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
    check(!(await has(page, '[data-cfp-box], [data-cfp-reach]')) && !/permission/i.test(await text(page, '[data-cfw-body]')), 'the Setup screen carries no permission control or wording', 'a permission control showed on Setup');
    await page.evaluate((t) => window.DeskV1ConnectPermissionsStep.setTarget(t), NEW_X);
    await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
    await page.waitForSelector('[data-cfp-rows]');

    check((await text(page, '[data-cfw-title]')) === 'Permissions for X', 'title "Permissions for X"', 'title: ' + await text(page, '[data-cfw-title]'));
    check((await boxes(page)).join('|') === 'read|post', `two Desk choices on the X API route (${(await boxes(page)).join(', ')})`, 'boxes: ' + (await boxes(page)).join('|'));
    check(!(await box(page, 'read')) && !(await box(page, 'post')), 'a new account starts with Read and Post OFF', 'a new grant started on');
    const owners = await page.$$eval('[data-cfp-owner]', (e) => e.map((x) => x.textContent.trim()));
    check(owners.join('|') === 'Desk permission|Desk permission', 'each row says whose permission it is (Desk permission)', 'owners: ' + owners.join('|'));
    check(/Covers: Your posts\./.test(await text(page, '[data-cfp-box="read"] ~ .desk-v1-cfp-text')) && !/Mentions|Replies|metrics/.test(await text(page, '[data-cfp-box="read"] ~ .desk-v1-cfp-text')), 'API Read covers exactly "Your posts" (the one documented capability), nothing else', 'read coverage wrong');
    check(/Covers: Post only\. Not Reply, media or Article\./.test(await text(page, '[data-cfp-box="post"] ~ .desk-v1-cfp-text')), 'Post covers "Post only. Not Reply, media or Article."', 'post coverage wrong');
    check(await has(page, '[data-cfp-paid]') && /may cost money/.test(await text(page, '[data-cfp-paid]')) && /never switches to the API/.test(await text(page, '[data-cfp-paid]')), 'the API Read row carries the paid notice and says a failed browser read never switches to it', 'paid notice missing');
    check(!(await has(page, '[data-cfp-confirm]')), 'with both off there is no confirm notice', 'a confirm notice showed with both off');
    check(writes(srv).length === 0, 'arriving here sent no write (types and read-only lookups only)', 'a write went out: ' + JSON.stringify(writes(srv).map((r) => r.path)));
    await page.click('[data-cfw-details] > summary');
    const dt = await text(page, '[data-cfp-details]');
    check(/Read covers/.test(dt) && /Post covers/.test(dt) && /Not covered by Read or Post/.test(dt) && /Reply, media upload, Article and broad listening need their own later permission/.test(dt) && /Allowing Read never allows Post/.test(dt), 'Details: exact coverage, what Read/Post do not cover, Read never allows Post', 'details: ' + dt);
    await rules(page, 'Permissions (X API)');
    await shot(page, 'x_api', width);
    await fits(page, 'Permissions (X API)');

    // Post without Read: the consequence is stated next to the choices.
    await page.check('[data-cfp-box="post"]');
    await page.waitForSelector('[data-cfp-confirm]');
    check(/Clayrune cannot confirm a post went out, so it stays Submitted/.test(await text(page, '[data-cfp-confirm]')), 'Post allowed without Read: "Clayrune cannot confirm a post went out, so it stays Submitted."', 'confirm notice wrong');
    check(await page.evaluate(() => !!document.querySelector('[data-cfp-rows]').parentElement.querySelector('[data-cfp-confirm]')), 'the notice sits in the same body as the choices', 'notice is elsewhere');
    check(!(await box(page, 'read')), 'the screen does not grant the missing Read for the person', 'Read was switched on');
    await shot(page, 'x_api_post_no_read', width);
    await page.check('[data-cfp-box="read"]');
    await page.waitForFunction(() => !document.querySelector('[data-cfp-confirm]'));
    ok('allowing Read clears the notice');
    await page.uncheck('[data-cfp-box="read"]');
    await page.waitForSelector('[data-cfp-confirm]');
    ok('turning Read off brings it back');
    check(/Applied after you save the connection/.test(await text(page, '[data-cfp-pending]')), 'a changed choice says it is applied after the connection is saved', 'no pending line');
    check(writes(srv).length === 0, 'choosing sent nothing', 'choosing sent a write');

    // Continue writes nothing; Review's call applies.
    await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
    check(writes(srv).length === 0, 'Continue to Review sent no write', 'Continue wrote');
    const sum = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(sum.kind === 'account' && sum.pending === true && sum.lines.join('|') === 'Desk: Post through the API' && /stays Submitted/.test(sum.notice), 'summary() for Review: one pending line "Desk: Post through the API" and the notice', JSON.stringify(sum));

    // 9: the Desk write, its prompt, a cancel, a refusal, a retry.
    let r = await applyWith(page, 'acct-new', ['cancel']);
    check(r.desk.status === 'cancelled' && r.site.status === 'not_attempted' && r.ok === false && logOf(srv, '/api/desk/connect/permissions/commit').length === 0, 'cancelling the passcode prompt sends nothing and reports "cancelled"', JSON.stringify(r));
    const words = await page.evaluate((x) => window.DeskV1ConnectPermissionsStep.outcome(true, x), r);
    check(words.kind === 'partial' && /^Connection saved; permissions unchanged: the passcode was not entered\./.test(words.lines[0]), 'the words say "Connection saved; permissions unchanged", never "connected"', JSON.stringify(words));
    srv.commitMode = 'refuse';
    r = await applyWith(page, 'acct-new', ['ok']);
    check(r.desk.status === 'failed' && /does not cover this exact capability/.test(r.desk.error), 'a refusal is a status with the server\'s words', JSON.stringify(r));
    srv.commitMode = 'ok';
    r = await applyWith(page, 'acct-new', ['ok']);
    const commits = logOf(srv, '/api/desk/connect/permissions/commit');
    check(r.desk.status === 'saved' && r.ok === true && commits.length === 2 && commits[0].body.request_id === commits[1].body.request_id, 'the retry of the same draft reuses the request id (a replay, never a second write)', `ids ${commits.map((c) => c.body.request_id).join(' / ')} ${JSON.stringify(r)}`);
    const d = commits[1].body.draft;
    check(JSON.stringify(d) === JSON.stringify({ account_id: 'acct-new', service: 'x', account_kind: 'account', read: false, post: true, scopes: [POST] }),
      'the draft names exactly publish/post on x-oauth: no Reply, media, Article or metrics scope', 'draft: ' + JSON.stringify(d));
    check(!('passcode' in d) && !('approved' in d), 'the draft carries no passcode or approval flag of its own', 'draft has extra fields');
    check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── 5-6: preserve existing grants ───────────────────────────────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: acct('acct-1') });
    check(await box(page, 'read') && await box(page, 'post'), 'an account\'s existing Read and Post show as ON (nothing is reset)', 'existing grants were not shown');
    check(!(await has(page, '[data-cfp-confirm]')), 'Read and Post both allowed: no confirm notice', 'notice with Read allowed');
    await page.click('[data-cfw-details] > summary');
    const dt = await text(page, '[data-cfp-details]');
    check(/Kept as they are/.test(dt) && /Your posts \(read\) via x-browser/.test(dt), 'Details lists the grant on the other route as kept (own posts via x-browser)', 'details: ' + dt);
    await page.click('[data-cfw-page="next"]');
    const dt2 = await text(page, '[data-cfp-details]');
    check(/Existing routes/.test(dt2) && /X API: Your posts/.test(dt2) && /Split routes stay as they are/.test(dt2), 'Details (page 2) lists the account\'s existing bound routes and says split routes stay', 'bound routes missing: ' + dt2);
    await shot(page, 'x_api_existing', width);
    let r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-1'));
    check(r.desk.status === 'unchanged' && r.ok && logOf(srv, '/api/desk/connect/permissions/commit').length === 0 && !(await has(page, 'input[id^="hp-passcode-"]')), 'an untouched screen applies nothing: no prompt, no request', JSON.stringify(r));
    await page.uncheck('[data-cfp-box="post"]');
    r = await applyWith(page, 'acct-1', ['ok']);
    const d = logOf(srv, '/api/desk/connect/permissions/commit')[0].body.draft;
    check(r.desk.status === 'saved' && d.post === false && d.read === true && JSON.stringify(d.scopes) === JSON.stringify([BROWSER_READ, READ]),
      'turning Post off drops only Post: the x-browser read and the x-oauth read are kept', 'draft: ' + JSON.stringify(d));
    check(srv.policies['acct-1'].scopes.length === 2, 'the saved policy has the two kept scopes', JSON.stringify(srv.policies['acct-1']));
    check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: acct('acct-2') });
    check(!(await box(page, 'read')) && await box(page, 'post') && await has(page, '[data-cfp-confirm]'), 'a saved Post-only account shows the Desk-cannot-confirm notice on arrival', 'post-only notice missing');
    await ctx.close();
  }
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: acct('acct-3') });
    check(await has(page, '[data-cfp-state="legacy"]') && /works as it did before/.test(await text(page, '[data-cfp-state]')) && !(await box(page, 'read')), 'a legacy account says it works as before until a choice is recorded', 'legacy state missing');
    let r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-3'));
    check(r.desk.status === 'unchanged' && logOf(srv, '/api/desk/connect/permissions/commit').length === 0, 'untouched legacy: nothing is written, behavior is unchanged', JSON.stringify(r));
    await page.check('[data-cfp-box="read"]');
    r = await applyWith(page, 'acct-3', ['ok']);
    const d = logOf(srv, '/api/desk/connect/permissions/commit')[0].body.draft;
    check(r.desk.status === 'saved' && d.read === true && d.post === false && JSON.stringify(d.scopes) === JSON.stringify([READ]), 'touching a legacy account records exactly the choice (Read own posts); what was left off is now off', 'draft: ' + JSON.stringify(d));
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: acct('acct-4') });
    check(await has(page, '[data-cfp-state="invalid"]') && /everything is off/.test(await text(page, '[data-cfp-state]')) && !(await box(page, 'read')) && !(await box(page, 'post')), 'an unreadable policy says everything is off and shows both rows off', 'invalid state missing');
    await ctx.close();
  }

  // ── 2, 7, 9: X sign-in (browser): Desk Read through the pane + the profile's site grant ─
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'signin', target: NEW_X, signin: { profile: 'x-ronx' } });
    check((await boxes(page)).join('|') === 'read|site', `X sign-in offers Desk Read and the browser site, no Post (${(await boxes(page)).join(', ')})`, 'boxes: ' + (await boxes(page)).join('|'));
    const readText = await text(page, '[data-cfp-box="read"] ~ .desk-v1-cfp-text');
    check(/Covers: Your posts, Mentions, Replies, Post metrics\./.test(readText) && /Desk permission/.test(readText) && !(await has(page, '[data-cfp-paid]')), 'browser Read covers the four claimed capabilities, is a Desk permission, and carries no paid notice', 'read text: ' + readText);
    const siteText = await text(page, '[data-cfp-box="site"] ~ .desk-v1-cfp-text');
    check(/Browser permission \(sign-in "x-ronx"\)/.test(siteText) && /Read pages on x\.com/.test(siteText) && /not saved yet/.test(siteText), 'the site row is a Browser permission on the sign-in "x-ronx" and says the profile is not saved yet', 'site text: ' + siteText);
    check(!(await box(page, 'read')) && !(await box(page, 'site')), 'both start OFF', 'a browser grant started on');
    await page.click('[data-cfw-details] > summary');
    const dt = await text(page, '[data-cfp-details]');
    check(/Browser sign-in/.test(dt) && /answer about a page, never the page/.test(dt) && /Read covers/.test(dt) && /through the browser sign-in/.test(dt), 'Details: coverage "through the browser sign-in" and the profile facts', 'details: ' + dt);
    await rules(page, 'Permissions (X sign-in)');
    await shot(page, 'x_signin', width);
    await fits(page, 'Permissions (X sign-in)');
    await page.check('[data-cfp-box="site"]');
    let r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-new'));
    check(r.desk.status === 'unchanged' && r.site.status === 'deferred' && r.pending === true && logOf(srv, '/api/browser/profiles/x-ronx/agent-read').length === 0 && !(await has(page, 'input[id^="hp-passcode-"]')), 'a profile that is not saved yet defers the site grant: no prompt, no PUT', JSON.stringify(r));
    const words = await page.evaluate((x) => window.DeskV1ConnectPermissionsStep.outcome(true, x), r);
    check(words.kind === 'partial' && words.lines.some((l) => /Finish sign-in to apply Read permission/.test(l)), 'the result says to finish sign-in to apply Read', JSON.stringify(words));
    await ctx.close();
  }
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await toPermissions(page, { type: 'signin', target: acct('acct-2'), signin: { profile: 'x-main' } });
    check(/Agents get an answer about a page on x\.com through "x-main"\. They cannot click or post\./.test(await text(page, '[data-cfp-box="site"] ~ .desk-v1-cfp-text')), 'an existing profile\'s site row states what agents get and that they cannot click or post', 'site facts wrong');
    await page.check('[data-cfp-box="site"]');
    let r = await applyWith(page, 'acct-2', ['ok']);
    const puts = logOf(srv, '/api/browser/profiles/x-main/agent-read');
    check(r.desk.status === 'unchanged' && r.site.status === 'saved' && puts.length === 1 && JSON.stringify(puts[0].body.domains) === JSON.stringify(['example.org', 'x.com']) && puts[0].body.enabled === true,
      'the site grant is ONE prompt and a PUT that keeps the profile\'s other site (example.org) and adds x.com', JSON.stringify({ r, puts: puts.map((p) => p.body) }));
    // Both: Desk first, then the site; cancelling the first stops the second.
    await page.check('[data-cfp-box="read"]');
    await page.uncheck('[data-cfp-box="site"]');
    srv.log.length = 0;
    r = await applyWith(page, 'acct-2', ['cancel']);
    check(r.desk.status === 'cancelled' && r.site.status === 'not_attempted' && logOf(srv, '/api/browser/profiles/x-main/agent-read').filter((x) => x.method === 'PUT').length === 0, 'cancelling the Desk prompt does not go on to the site prompt', JSON.stringify(r));
    r = await applyWith(page, 'acct-2', ['ok', 'ok']);
    const order = srv.log.filter((x) => x.method !== 'GET').map((x) => x.path);
    check(r.ok && r.desk.status === 'saved' && r.site.status === 'saved' && order.join('|') === '/api/desk/connect/permissions/commit|/api/browser/profiles/x-main/agent-read', 'Desk and site are two prompts, the Desk write first, each with its own passcode', JSON.stringify({ r, order }));
    await ctx.close();
  }

  // ── 2: LinkedIn sign-in: the Desk runs this route for nothing, so only the site row ────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { address: 'linkedin.com', type: 'signin', target: { kind: 'member', identity: 'ron', account: { new: { identity: 'ron', label: '' } } }, signin: { profile: 'li-ron' } });
    check((await boxes(page)).join('|') === 'site', `LinkedIn sign-in offers the site row only: no Desk Read, no Post (${(await boxes(page)).join(', ')})`, 'boxes: ' + (await boxes(page)).join('|'));
    check(/^Allow agents to read through this sign-in\. Posting is not enabled by signing in\.$/.test(await text(page, '[data-cfw-copy]')), 'the copy says posting is not enabled by signing in', await text(page, '[data-cfw-copy]'));
    await page.click('[data-cfw-details] > summary');
    const dt = await text(page, '[data-cfp-details]');
    check(/The Desk does not run this route for any purpose yet/.test(dt), 'Details: the Desk does not run this route for any purpose yet', 'details: ' + dt);
    await page.click('[data-cfw-page="next"]');
    const dt2 = await text(page, '[data-cfp-details]');
    check(/does not read your LinkedIn feed or notifications by itself/.test(dt2), 'Details (page 2): reading the site does not read the LinkedIn feed', 'details page 2: ' + dt2);
    await shot(page, 'linkedin_signin', width);
    await ctx.close();
  }

  // ── 7: saved login is a fact, not a switch ──────────────────────────────
  for (const [login, expect] of [['x.ron', /Unattended agents may use "x\.ron"/], ['x.quiet', /Unattended agents may not use "x\.quiet"/]]) {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'signin', target: NEW_X, signin: { login } });
    await page.waitForSelector('[data-cfp-vault]');
    await page.waitForFunction(() => /Unattended agents/.test((document.querySelector('[data-cfp-vault]') || {}).textContent || ''), null, { timeout: 4000 });
    const t = await text(page, '[data-cfp-vault]');
    check(expect.test(t) && /Change this in Secrets/.test(t) && /Filling the password always starts with you/.test(t) && /Saved login \(Secrets\)/.test(t), `saved login "${login}": the vault's unattended-use setting is stated (${t.slice(0, 60)}…)`, 'vault: ' + t);
    check((await page.$$('[data-cfp-vault] input, [data-cfp-vault] button')).length === 0, 'the vault fact has no control: it is changed in Secrets', 'a control was drawn for the vault');
    check(srv.log.every((r) => !(/\/api\/secrets/.test(r.path) && r.method !== 'GET')), 'only the metadata list was read, no value', 'a vault write or value read went out');
    await ctx.close();
  }

  // ── 10: MCP, whole-server approval ──────────────────────────────────────
  {
    const { ctx, page, srv, pageErrors } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'mcp' });
    check((await text(page, '[data-cfw-title]')) === 'Permissions' && (await text(page, '[data-cfp-reach] legend')) === 'Use this server\'s tools', 'custom MCP: "Use this server\'s tools"', 'mcp heading wrong');
    const body = await text(page, '[data-cfw-body]');
    check((await boxes(page)).length === 0 && !/\b(read|post)\b/i.test(body), 'no Read or Post switch and no such wording in the body: the server is approved whole', 'MCP body: ' + body);
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'Continue is off until a project is chosen (project is the default reach)', 'Continue on with no project');
    const first = srv.fx.projects[0];
    await page.selectOption('[data-cfp-project]', first.id);
    check(!(await page.$eval('[data-cfw-primary]', (b) => b.disabled)) && JSON.stringify(await page.evaluate(() => window.DeskV1ConnectPermissionsStep.reach())) === JSON.stringify({ scope: 'project', project_id: first.id }), 'choosing a project turns Continue on; reach() = project', 'project reach wrong');
    await page.check('[data-cfp-scope][value="global"]');
    await page.waitForSelector('[data-cfp-wide]');
    check(/Wider reach/.test(await text(page, '[data-cfp-wide]')) && /Agents in every project can use it/.test(await text(page, '[data-cfp-wide-note]')) && JSON.stringify(await page.evaluate(() => window.DeskV1ConnectPermissionsStep.reach())) === JSON.stringify({ scope: 'global' }), 'all projects is flagged "Wider reach" and says who can use it; reach() = global', 'global reach wrong');
    await page.click('[data-cfw-details] > summary');
    check(/A server's tools cannot be made read-only by filtering their names/.test(await text(page, '[data-cfp-details]')) && /Campaign approval and LinkedIn's closed posting gate are unchanged/.test(await text(page, '[data-cfp-details]')), 'Details says why there is no Read/Post switch and that campaign approval and the LinkedIn gate are unchanged', 'mcp details wrong');
    await rules(page, 'Permissions (MCP)');
    await shot(page, 'mcp', width);
    await fits(page, 'Permissions (MCP)');
    const r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('x'));
    check(r.ok && r.desk.status === 'unchanged' && writes(srv).length === 0 && logOf(srv, '/api/desk/connect/permissions/x').length === 0, 'MCP sends nothing to the Desk policy and nothing else from this screen', JSON.stringify(r));
    check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── 11: reference-only grants nothing ───────────────────────────────────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: NEW_X });
    await page.evaluate(() => { window.__fx.api.select({ type: 'reference' }); window.__fx.api.go('permissions'); });
    await page.waitForFunction(() => /grants no connection permissions/.test((document.querySelector('[data-cfw-copy]') || {}).textContent || ''), null, { timeout: 4000 });
    check((await boxes(page)).length === 0 && !(await has(page, '[data-cfp-reach]')) && /Saving information grants no connection permissions\. Credential access follows its existing vault policy\./.test(await text(page, '[data-cfw-copy]')), 'a reference-only record shows no control and says it grants no connection permission', 'reference screen wrong');
    const sum = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    const r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-1'));
    check(sum.kind === 'reference' && sum.pending === false && r.ok && r.desk.status === 'unchanged' && writes(srv).length === 0 && logOf(srv, '/api/desk/connect/permissions/acct-1').length === 0, 'summary says nothing is granted and apply() sends nothing', JSON.stringify({ sum, r }));
    await shot(page, 'reference', width);
    await ctx.close();
  }

  // ── 3: a change of account / Close drops the choices ────────────────────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: NEW_X });
    await page.check('[data-cfp-box="read"]');
    await page.evaluate(() => window.__fx.api.select({ account: 'someone-else' }));
    const after = await page.evaluate(() => ({ sum: window.DeskV1ConnectPermissionsStep.summary(), r: null }));
    const r = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-new'));
    check(after.sum === null && r.ok && r.desk.status === 'unchanged' && writes(srv).length === 0, 'changing the account drops the choices: nothing of the old branch can be applied', JSON.stringify({ after, r }));
    await ctx.close();
  }

  // ── 12: 200% text ────────────────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toPermissions(page, { type: 'api', target: acct('acct-2') });
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    await fits(page, 'Permissions at 200% text');
    await page.click('[data-cfw-details] > summary');
    await fits(page, 'Permissions with Details at 200% text');
    await shot(page, 'x_api_200pct', width);
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1440, 900);
  await run(browser, 390, 844);
} finally { await browser.close(); }
console.log(bad ? `\n${bad} FAILED` : '\nall passed');
process.exit(bad ? 1 : 0);
