#!/usr/bin/env node
/**
 * Desk v1 — a known service's routes by purpose, and one account bound per purpose
 * (docs/DESK_SERVICE_PROFILES_SPEC.md sections 3.3 and 4, slice P2), the browser half, against a fake
 * server, `desk_v1_live` ON. The server half is pinned by tests/test_desk_purposes.py. The routes shown are
 * the real profile's (read through Python once), so a profile change moves this smoke with it.
 *
 *   X          two identities; API publish plus pane own reads (and one own read through the API); a route
 *              Clayrune cannot run is described and has no box; nothing is ticked for the person; no write
 *              before Save; a wrong passcode keeps the draft; the same request id on the retry; Save does
 *              not mark anything verified; "Check now" proves part of a purpose and reads "Partly verified";
 *              the second identity starts empty.
 *   LinkedIn   a member and a Company Page stay separate; a new member account is made by the Save; no
 *              LinkedIn publish route can be chosen.
 *   + phone    no horizontal scroll, the Save routes button reachable at 390px.
 *
 * Screenshots: docs/desk_v1/screens/connect_purpose_{x,x_second,linkedin}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-purpose.mjs
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
const PY = (code) => JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c',
  `import json, sys; sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})\n${code}`],
  { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const REGISTRY = PY('from mc.desk_connect import registry; print(json.dumps(registry.v1_projection()))');
const VIEWS = PY(`from mc.desk_connect import purpose_view, purpose_bindings, registry
out = {}
for sid in ('x', 'linkedin'):
    p = registry.profile(sid)
    out[sid] = {'service': {'id': sid, 'label': p['label']}, 'revision': p['revision'], 'account_kinds': p['account_kinds'],
                'change_summary': p['change_summary'], 'new_account_allowed': sid in purpose_bindings._NEW_ACCOUNT_SERVICES,
                'purposes': purpose_view.purposes(p)}
print(json.dumps(out))`);
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

const covered = (view, purpose, route, kind) => {
  const r = view.purposes.find((p) => p.id === purpose).routes.find((x) => x.id === route);
  return Object.entries(r.coverage[kind] || {}).filter(([, s]) => s === 'documented' || s === 'claimed').map(([c]) => c);
};

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, services: [], seen: new Map(), bound: {}, verified: {} };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'linkedin')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.kindOf = (a) => {
    if (a.platform === 'x') return 'account';
    const first = Object.values(srv.bound[a.id] || {})[0];
    return first ? first.account_kind : (a.id === 'ch-li-page' ? 'organization' : null);
  };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  srv.view = (sid) => {
    const v = VIEWS[sid];
    const accounts = srv.accounts.filter((a) => a.platform === sid).map((a) => {
      const mine = srv.bound[a.id] || {};
      const groups = {};
      Object.entries(mine).forEach(([key, b]) => {
        const [purpose, cap] = key.split('.');
        const g = (groups[`${purpose}:${b.route_id}`] = groups[`${purpose}:${b.route_id}`] || { purpose, route_id: b.route_id, capabilities: [], refs: b.refs, approved_at: 'now', account_kind: b.account_kind, revision: v.revision });
        g.capabilities.push(cap);
      });
      const bound = Object.values(groups).map((g) => {
        const r = v.purposes.find((p) => p.id === g.purpose).routes.find((x) => x.id === g.route_id);
        const setup = !r.status.executes ? { setup: 'pending_runtime', reason: 'Saved. Clayrune cannot run this route for this purpose yet.' }
          : (r.transport === 'browser' && !(g.refs || {}).browser_profile ? { setup: 'needs_signin', reason: 'no saved browser profile' } : { setup: 'ready', reason: null });
        return { ...g, route_title: r.title, gone_from_profile: false, ...setup };
      });
      const verification = {};
      ['publish', 'read_own'].forEach((purpose) => {
        const caps = Object.keys(mine).filter((k) => k.startsWith(purpose + '.')).map((k) => k.split('.')[1]).sort();
        const ver = caps.filter((c) => srv.verified[`${a.id}:${purpose}:${c}`]);
        verification[purpose] = { state: caps.length && ver.length === caps.length ? 'verified' : ver.length ? 'partial' : 'not_checked', verified: ver, bound: caps, capabilities: {} };
      });
      return { id: a.id, label: a.label, identity: a.identity, account_kind: srv.kindOf(a), organization_id: a.id === 'ch-li-page' ? '998877' : null, read_via: 'pane', bound, verification };
    });
    return { ...v, accounts, vault: [{ name: `${sid}.login`, entry_type: 'login', has_username: true, scope: 'global', matches: true }, { name: 'other.key', entry_type: 'api_key', has_username: false, scope: 'global', matches: false }] };
  };
  return srv;
}

function inspectFake(raw) {
  let u;
  try { u = new URL(raw); } catch (_) { return { status: 400, body: { error: 'That is not a web address.', hint: '' } }; }
  const host = u.hostname.toLowerCase();
  const svc = REGISTRY.services.find((s) => s.hosts.includes(host)) || null;
  const rows = (svc ? svc.options : []).concat(REGISTRY.common_options, [{
    method: 'save_for_agents', support: 'available', title: 'Save for agents', evidence: 'Always available',
    guidance: 'Saves a note for agents.',
  }]).map((o) => ({ ...o, selectable: o.method === 'save_for_agents' }));
  return { status: 200, body: { url: `https://${host}${u.pathname === '/' ? '' : u.pathname}`, host, path: u.pathname,
    service: svc ? { id: svc.id, label: svc.label } : null, options: rows } };
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
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/inspect' && method === 'POST') { const r = inspectFake((body || {}).input); return J(r.body, r.status); }
    if (path === '/api/desk/connect/purposes' && method === 'POST') return J(srv.view(body.service));
    if (path === '/api/desk/connect/purpose/verify' && method === 'POST') {
      const mine = srv.bound[body.account_id] || {};
      const routes = [];
      const ids = [...new Set(Object.entries(mine).filter(([k]) => k.startsWith(body.purpose + '.')).map(([, b]) => b.route_id))];
      ids.forEach((rid) => {
        const caps = Object.entries(mine).filter(([k, b]) => k.startsWith(body.purpose + '.') && b.route_id === rid).map(([k]) => k.split('.')[1]);
        if (rid === 'x-browser' && caps.includes('mentions')) { srv.verified[`${body.account_id}:${body.purpose}:mentions`] = true; routes.push({ route_id: rid, result: 'passed', proved: ['mentions'], not_proved: caps.filter((c) => c !== 'mentions') }); }
        else routes.push({ route_id: rid, result: 'no_check', message: 'Clayrune has no free read-only check for this route, and does not spend a billed call to prove it.' });
      });
      return J({ account_id: body.account_id, purpose: body.purpose, routes });
    }
    if (path === '/api/desk/connect/purpose/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.seen.has(body.request_id)) return J({ ok: true, duplicate: true, ...srv.seen.get(body.request_id) }, 200);
      const d = body.draft;
      let id = d.account.id;
      let created = false;
      if (!id) {
        id = 'ch-li-new'; created = true;
        srv.accounts.push({ id, platform: d.service, identity: d.account.new.identity, label: d.account.new.label || d.account.new.identity, capability: 'draft_only', voice: '', publish: { ready: false, reason: 'not signed in', secret: null, unattended_ok: null } });
      }
      srv.bound[id] = srv.bound[id] || {};
      d.bindings.forEach((b) => b.capabilities.forEach((c) => {
        const key = `${b.purpose}.${c}`;
        if (!b.route_id) { delete srv.bound[id][key]; return; }
        srv.bound[id][key] = { route_id: b.route_id, account_kind: d.account_kind, refs: { ...(b.browser_profile ? { browser_profile: b.browser_profile } : {}), ...(b.credentials || {}) } };
      }));
      const ways = new Set(Object.entries(srv.bound[id]).filter(([k]) => k.startsWith('read_own.')).map(([, b]) => (b.route_id === 'x-oauth' ? 'api' : 'pane')));
      const out = { account_id: id, account_created: created, legacy_read: { read_via: 'pane', split: ways.size > 1 } };
      srv.seen.set(body.request_id, out);
      return J({ ok: true, duplicate: false, ...out }, 201);
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
const commits = (srv) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/desk/connect/purpose/commit');
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|purposes)$/.test(r.path));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_purpose_${name}_${w}.png`) });

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    const sv = document.querySelector('[data-cp-save]');
    if (sv) sv.scrollIntoView({ block: 'center' });
    const r = sv ? sv.getBoundingClientRect() : null;
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
      save: r ? { left: r.left, right: r.right } : null, vw: window.innerWidth };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  if (m.save) check(m.save.left >= 0 && m.save.right <= m.vw, `${label}: Save routes is inside the ${m.vw}px window`, `${label}: Save routes outside the window ${JSON.stringify(m.save)}`);
}

async function openService(page, address) {
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  await page.fill('[data-cf-url]', address);
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.waitForSelector('[data-cp]', { timeout: 5000 });
}
const tick = (page, key) => page.check(`[data-cp-cap="${key}"]`);
const passcodePrompt = async (page, code) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', code);
  await page.click('.modal-content .btn-add');
};

async function xScenario(browser, width, height) {
  console.log(`X at ${width}px: API publish plus pane own reads`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openService(page, 'https://x.com/ron');
  const V = VIEWS.x;
  const purposes = await page.$$eval('[data-cp-purpose]', (e) => e.map((x) => x.dataset.cpPurpose));
  check(purposes.length === 0, 'no purpose is shown until an account is chosen', 'purposes shown early: ' + purposes);
  const accounts = await page.$$eval('[data-cp-account] option', (e) => e.map((o) => o.value).filter(Boolean));
  check(accounts.join() === 'ch-x-ron,ch-x-clayrune', `both X identities are offered (${accounts.join()})`, 'accounts: ' + accounts.join());
  await page.selectOption('[data-cp-account]', 'ch-x-ron');
  await page.waitForSelector('[data-cp-purpose="read_own"]');
  const shown = await page.$$eval('[data-cp-purpose]', (e) => e.map((x) => x.dataset.cpPurpose));
  check(shown.join() === 'publish,read_own', `routes are grouped by purpose, per account (${shown.join()}; workspace-wide listening is not per account)`, 'purposes: ' + shown);
  const rows = await page.$$eval('[data-cp-route]', (e) => e.map((r) => [r.dataset.cpPurposeOf, r.dataset.cpRoute, r.dataset.cpExecutes, r.dataset.cpBindable]));
  check(rows.some((r) => r.join() === 'publish,x-oauth,true,true') && rows.some((r) => r.join() === 'read_own,x-browser,true,true') && rows.some((r) => r.join() === 'read_own,x-oauth,true,true'),
    'the API and the pane are both offered, each marked as something Clayrune runs', 'rows: ' + JSON.stringify(rows));
  check(rows.some((r) => r.join() === 'publish,x-manual,false,false') && rows.some((r) => r.join() === 'publish,x-mcp-action,false,false'),
    'manual and MCP routes are described and marked "cannot be chosen"', 'rows: ' + JSON.stringify(rows));
  check((await page.$$('[data-cp-route="x-manual"] input')).length === 0 && (await page.$$('[data-cp-route="x-mcp-action"] input')).length === 0, 'a route Clayrune cannot run has no box to tick', 'a box exists for a manual/MCP route');
  check((await page.$$('[data-cp-cap]:checked')).length === 0 && await page.$eval('[data-cp-save]', (b) => b.disabled), 'nothing is ticked for the person and Save routes is off', 'something was pre-ticked');
  const cost = await page.$eval('[data-cp-purpose="publish"] [data-cp-route="x-oauth"] [data-cp-cost]', (e) => e.textContent.trim());
  check(cost.length > 3, `the API route shows its cost line ("${cost}")`, 'no cost line on the API route');
  check((await page.$$('[data-cp-purpose="publish"] [data-cp-route="x-oauth"] [data-cp-evidence]')).length === 1, 'every route carries its evidence line', 'evidence line missing');
  check(writes(srv).length === 0, 'nothing has been written (only inspect and purposes were called)', 'writes so far: ' + JSON.stringify(writes(srv).map((r) => r.path)));

  const apiOwn = covered(V, 'read_own', 'x-oauth', 'account')[0];
  const paneCaps = covered(V, 'read_own', 'x-browser', 'account').filter((c) => c !== apiOwn);
  await tick(page, 'publish:post:x-oauth');
  await tick(page, `read_own:${apiOwn}:x-browser`);
  await tick(page, `read_own:${apiOwn}:x-oauth`);
  check(!(await page.isChecked(`[data-cp-cap="read_own:${apiOwn}:x-browser"]`)), `ticking ${apiOwn} on the API route unticks it on the pane route (one route per capability)`, 'the same capability stayed on two routes');
  for (const c of paneCaps) await tick(page, `read_own:${c}:x-browser`);
  await page.waitForSelector('[data-cp-prof="x-browser"]');
  await page.fill('[data-cp-prof="x-browser"]', 'x-ron');
  await page.dispatchEvent('[data-cp-prof="x-browser"]', 'change');
  await page.waitForSelector('[data-cp-summary]');
  const summary = await page.$eval('[data-cp-summary]', (e) => e.innerText);
  check(/Publish: post via/.test(summary) && new RegExp(apiOwn).test(summary) && /browser profile x-ron/.test(summary), 'the summary names each route and the profile, before Save', 'summary: ' + summary);
  check(writes(srv).length === 0, 'still nothing written before Save', 'writes before Save');
  await shot(page, 'x', width);
  await fits(page, 'X purposes');

  await page.click('[data-cp-save]');
  await passcodePrompt(page, 'wrong-passcode');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  await page.click('.modal-content .btn-secondary');
  check(commits(srv).length === 1 && Object.keys(srv.bound).length === 0, 'a wrong passcode reaches the server once and saves nothing', 'commits ' + commits(srv).length + ' bound ' + JSON.stringify(srv.bound));
  check(await page.isChecked('[data-cp-cap="publish:post:x-oauth"]') && (await page.inputValue('[data-cp-prof="x-browser"]')) === 'x-ron', 'the draft is still there after a wrong passcode', 'the draft was lost');
  await page.click('[data-cp-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cp-status][data-cf-msg="ok"]', { timeout: 6000 });
  const sent = commits(srv);
  check(sent.length === 2 && sent[0].body.request_id === sent[1].body.request_id, 'the retry reuses the same request id', 'ids: ' + sent.map((c) => c.body.request_id));
  const b = sent[1].body.draft.bindings;
  check(sent[1].body.draft.account.id === 'ch-x-ron' && sent[1].body.draft.account_kind === 'account'
    && b.some((x) => x.purpose === 'publish' && x.route_id === 'x-oauth' && x.capabilities.join() === 'post')
    && b.some((x) => x.purpose === 'read_own' && x.route_id === 'x-oauth' && x.capabilities.join() === apiOwn)
    && b.some((x) => x.purpose === 'read_own' && x.route_id === 'x-browser' && x.browser_profile === 'x-ron' && x.capabilities.slice().sort().join() === paneCaps.slice().sort().join()),
    'Save posts API publish, one API own read and the pane reads, with the profile name and no credential value', 'bindings: ' + JSON.stringify(b));
  check(!JSON.stringify(sent[1].body).includes('"value"'), 'no credential value is in the request', 'a value field was sent');
  const verify = await page.$$eval('[data-cp-verify]', (e) => e.map((x) => x.dataset.cpVerify));
  check(verify.length === 2 && verify.every((v) => v === 'not_checked'), 'Save marks nothing verified (both purposes read "Not checked")', 'verify states: ' + verify);
  check((await page.$$('[data-cp-bound="x-browser"] [data-cp-setup]')).length === 1 && (await page.$$('[data-cp-bound="x-oauth"]')).length === 2, 'the saved routes are listed under their purposes with their setup state', 'bound rows wrong');
  await page.click('[data-cp-check="read_own"]');
  await page.waitForSelector('[data-cp-verify="partial"]', { timeout: 5000 });
  const partial = await page.$eval('[data-cp-state="read_own"] [data-cp-verify]', (e) => e.textContent.trim());
  check(/Partly verified: 1 of \d/.test(partial), `"Check now" proves one capability and reads "${partial}", not Verified`, 'partial text: ' + partial);
  check((await page.$$eval('[data-cp-verify]', (e) => e.map((x) => x.dataset.cpVerify))).includes('not_checked'), 'the publish purpose is still Not checked: a billed call is not a probe', 'publish was marked');
  const ownMsg = await page.$eval('[data-cp-state="read_own"] [data-cp-checkmsg]', (e) => e.textContent);
  check(/no free read-only check/.test(ownMsg) && /passed/.test(ownMsg), 'the check says plainly which route had no check and which passed', 'check text: ' + ownMsg);
  await page.selectOption('[data-cp-account]', 'ch-x-clayrune');
  await page.waitForSelector('[data-cp-state="publish"]');
  check((await page.textContent('[data-cp-state="publish"]')).includes('Nothing is chosen') && !srv.bound['ch-x-clayrune'], 'the second X identity starts with nothing chosen and the first keeps its routes', 'second identity not empty');
  await shot(page, 'x_second', width);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function liScenario(browser, width, height) {
  console.log(`LinkedIn at ${width}px: member and Company Page stay separate`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openService(page, 'https://www.linkedin.com/in/ron');
  const V = VIEWS.linkedin;
  const before = srv.accounts.length;
  await page.selectOption('[data-cp-account]', '__new');
  await page.waitForSelector('[data-cp-kind]');
  await page.selectOption('[data-cp-kind]', 'member');
  await page.fill('[data-cp-new-identity]', 'Ron Levy');
  await page.dispatchEvent('[data-cp-new-identity]', 'change');
  await page.waitForSelector('[data-cp-purpose="read_own"]');
  check((await page.$$('[data-cp-purpose="publish"] [data-cp-cap]')).length === 0, 'no LinkedIn publish route can be chosen (the API is restricted, the rest are a person or not available)', 'a publish box exists for LinkedIn');
  const why = await page.$$eval('[data-cp-purpose="publish"] [data-cp-why]', (e) => e.map((x) => x.textContent.trim()));
  check(why.length >= 2 && why.every((t) => t.length > 10), 'each publish route says why it cannot be chosen', 'reasons: ' + JSON.stringify(why));
  check((await page.$$('[data-cp-route="linkedin-oauth"]')).length === 0, 'the Company Page route is not shown for a member account', 'the Page route showed for a member');
  const memberCaps = covered(V, 'read_own', 'linkedin-browser', 'member');
  await tick(page, `read_own:${memberCaps[0]}:linkedin-browser`);
  await page.fill('[data-cp-prof="linkedin-browser"]', 'li-ron');
  await page.dispatchEvent('[data-cp-prof="linkedin-browser"]', 'change');
  await shot(page, 'linkedin', width);
  await fits(page, 'LinkedIn purposes');
  check(writes(srv).length === 0 && srv.accounts.length === before, 'no account is made and nothing is written before Save', 'writes before Save');
  await page.click('[data-cp-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cp-status][data-cf-msg="ok"]', { timeout: 6000 });
  const body = commits(srv)[0].body.draft;
  check(body.account.new.identity === 'Ron Levy' && body.account_kind === 'member' && srv.accounts.some((a) => a.id === 'ch-li-new'), 'the Save made the member account and bound it', 'draft: ' + JSON.stringify(body));
  check(!!srv.bound['ch-li-new'] && Object.values(srv.bound['ch-li-new']).every((x) => x.account_kind === 'member'), 'the member holds member bindings', 'bound: ' + JSON.stringify(srv.bound));
  await page.selectOption('[data-cp-account]', 'ch-li-page');
  await page.waitForSelector('[data-cp-state="read_own"]');
  check((await page.$$('[data-cp-kind]')).length === 0, 'the Company Page account is already known as a Page: its kind is not asked', 'kind asked for a Page');
  check((await page.$$('[data-cp-purpose="read_own"] [data-cp-route="linkedin-oauth"]')).length === 1 && (await page.textContent('[data-cp-state="read_own"]')).includes('Nothing is chosen') && !srv.bound['ch-li-page'], 'the Page shows its own routes, with nothing chosen, apart from the member', 'the Page is not separate');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await xScenario(browser, 1440, 900);
  await xScenario(browser, 390, 844);
  await liScenario(browser, 1440, 900);
  await liScenario(browser, 390, 844);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
