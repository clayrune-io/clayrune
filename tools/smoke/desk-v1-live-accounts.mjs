#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S5) — Connections and the ⑤ Where board against a fake
 * server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers (the routes themselves are pinned by tests/test_desk_accounts.py):
 *   1. Connections -> a row paints the SERVER's derived `publish` ("Publishing: not
 *                     connected (reason)"), a step-by-step `Connect X` on X only (a reason that
 *                     names the vault is not shown; the steps say it in plain words), no fixture Connect button, the extra channels as placeholders.
 *   2. Add / remove-> the form POSTs M3 with no credential field; the server's answer is
 *                     adopted; Undo DELETEs; ✕ DELETEs M5; a refused remove is rolled back.
 *   3. Read via    -> PATCHes M4 `/api/desk/accounts/<id>`, filed under the project that uses it.
 *   4. Where       -> adding a column PATCHes the campaign's plan, a version add is M17,
 *                     a move is M18 `account_id`, removing a column archives its pending
 *                     versions; an off source names the server's reason; no card has a time.
 *   5. Flag OFF    -> the same gestures make 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-accounts.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const PUBLISH = {
  x: { ready: false, reason: 'no X API token in the vault', secret: 'x.api', unattended_ok: null },
  linkedin: { ready: false, reason: 'LinkedIn organization posting is not approved yet (w_organization_social)', secret: null, unattended_ok: null },
};

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = { log: [], next: {}, fx, accounts: [], pieces: fx.families.map((f) => JSON.parse(JSON.stringify(f))) };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'linkedin' || c.platform === 'blog')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: PUBLISH[c.platform] || { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, accounts: srv.accounts, pieces: srv.pieces });
  return srv;
}

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
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
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/accounts' && method === 'POST') {
      const a = { id: body.id, platform: body.platform, identity: body.identity, label: body.label || body.identity, capability: body.capability, voice: '',
        connected: false, publish: PUBLISH[body.platform] || { ready: true, reason: null, secret: null, unattended_ok: null } };
      srv.accounts.push(a);
      return J(a, 201);
    }
    let m = path.match(/^\/api\/desk\/accounts\/([^/]+)$/);
    if (m) {
      const i = srv.accounts.findIndex((x) => x.id === m[1]);
      if (i < 0) return J({ error: 'account not found' }, 404);
      if (method === 'DELETE') { srv.accounts.splice(i, 1); return J({ ok: true }); }
      if (method === 'PATCH') { Object.assign(srv.accounts[i], body, { project_id: undefined }); return J(srv.accounts[i]); }
    }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'PATCH') return J({ ...(srv.fx.campaigns.find((c) => c.id === m[1]) || { id: m[1] }), ...body });
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions$/);
    if (m && method === 'POST') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      if (!p) return J({ error: 'piece not found' }, 404);
      p.versions = (p.versions || []).concat([{ id: body.id, channelId: body.account_id, state: 'drafting', revision: 0 }]);
      return J(p, 201);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const v = p && (p.versions || []).find((x) => x.id === m[2]);
      if (!v) return J({ error: 'version not found' }, 404);
      if (['approved', 'scheduled', 'sending', 'submitted', 'verified_published'].includes(body.state)) return J({ error: 'approval is its own human action' }, 400);
      if ('account_id' in body) v.channelId = body.account_id;
      if ('state' in body) v.state = body.state;
      return J(p);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  // secrets-panel.js declares openSecretsVault itself, so the stub goes in after boot.
  await page.evaluate(() => { window.openSecretsVault = () => { window.__vaultOpened = (window.__vaultOpened || 0) + 1; }; });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const openConnections = async (page) => {
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-account]', { timeout: 6000 });
};
const openWhere = async (page, campaignId = 'camp-1') => {
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, panel: 'where' }), campaignId);
  await page.waitForSelector('.desk-v1-where[data-where]', { timeout: 6000 });
};
const stateOf = (page, famId, vId) => page.evaluate(([f, v]) => {
  const fam = window.DeskV1Store.state().families.find((x) => x.id === f);
  const ver = fam && (fam.versions || []).find((x) => x.id === v);
  return ver ? { channelId: ver.channelId, state: ver.state } : null;
}, [famId, vId]);

// ── 1: Connections reads the derived state ─────────────────────────────────
async function connectionsRead(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openConnections(page);

  const rows = await page.$$eval('[data-conn-account]', (els) => els.map((e) => ({
    id: e.dataset.connAccount, state: e.dataset.connState,
    publish: (e.querySelector('[data-conn-publish-text]') || {}).textContent || '',
    secrets: !!e.querySelector('[data-conn-x-guide]'), action: !!e.querySelector('[data-conn-action]'),
  })));
  const x = rows.find((r) => r.id === 'ch-x-ron');
  const li = rows.find((r) => r.id === 'ch-li-page');
  check(rows.length === srv.accounts.length, `the rows are the server's accounts (${rows.length})`, 'rows: ' + JSON.stringify(rows.map((r) => r.id)));
  check(x && x.state === 'off' && /^not connected$/.test(x.publish.trim()), `X: Publishing reads "not connected" (a vault-naming reason is left to the steps) ("${x && x.publish}")`, 'X row: ' + JSON.stringify(x));
  check(li && li.state === 'off' && /w_organization_social/.test(li.publish), 'the LinkedIn page shows NOT connected with the app-review reason, never faked', 'LinkedIn row: ' + JSON.stringify(li));
  check(x.secrets && !li.secrets, '`Connect X` appears on the X account and not on the LinkedIn page', 'secrets buttons: ' + JSON.stringify(rows.map((r) => [r.id, r.secrets])));
  check(rows.every((r) => !r.action), 'no fixture Connect / Reconnect button on any live row', 'a Connect button survived live');
  const typed = await page.$$('[data-connections] input[type="password"], [data-connections] input[name*="token" i], [data-connections] input[name*="secret" i]');
  check(typed.length === 0, 'no credential field exists on the screen until a guide is opened', 'a credential input is on Connections');
  await page.click('[data-conn-account="ch-x-ron"] [data-conn-x-guide]');
  await page.waitForSelector('[data-conn-account="ch-x-ron"] [data-conn-x-wizard]', { timeout: 4000 });
  check(!(await page.evaluate(() => window.__vaultOpened)), '`Connect X` opens its own steps, not the Secrets panel', 'the Secrets panel was opened');

  const ph = await page.$$eval('[data-conn-placeholder]', (els) => els.map((e) => e.dataset.connPlaceholder));
  check(JSON.stringify(ph) === JSON.stringify(['youtube', 'discord', 'reddit']), `YouTube / Discord / Reddit stay placeholder tiles (${ph})`, 'placeholders: ' + JSON.stringify(ph));
  const srcs = await page.$$eval('[data-conn-source]', (els) => els.map((e) => e.dataset.connSource));
  check(srcs.includes('gdrive') && srcs.includes('dropbox'), 'Google Drive and Dropbox stay placeholder source tiles', 'sources: ' + JSON.stringify(srcs));

  // Check again re-reads M2 and repaints with the new derived state.
  srv.accounts.find((a) => a.id === 'ch-x-ron').publish = { ready: true, reason: null, secret: 'x.api', unattended_ok: false };
  srv.log.length = 0;
  await page.click('[data-conn-recheck]');
  await settle(page, () => (document.querySelector('[data-conn-account="ch-x-ron"]') || {}).dataset.connState === 'ok');
  check(calls(srv, 'GET', /^\/api\/desk\/accounts$/).length === 1, 'Check again is one GET of M2 and the row repaints Connected', 'recheck calls: ' + JSON.stringify(srv.log));
  check(!!(await page.$('[data-conn-account="ch-x-ron"] [data-conn-unattended]')), 'a token that may not be used unattended says scheduled posts will be held', 'no unattended note');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 2: add / remove / read via ─────────────────────────────────────────────
async function addRemove(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openConnections(page);
  const before = srv.accounts.length;

  srv.log.length = 0;
  await page.selectOption('[data-conn-add-platform]', 'x');
  await page.fill('[data-conn-add-identity]', '@newhandle');
  await page.click('[data-conn-add-submit]');
  await settle(page, () => [...document.querySelectorAll('[data-conn-account]')].some((e) => /newhandle/.test(e.textContent) && !/checking/.test(e.textContent)));
  const post = calls(srv, 'POST', /^\/api\/desk\/accounts$/);
  check(post.length === 1 && post[0].body.platform === 'x' && post[0].body.identity === '@newhandle' && /^acct-/.test(post[0].body.id),
    'Add posts M3 with the client id, platform and identity', 'add POST: ' + JSON.stringify(post.map((r) => r.body)));
  check(Object.keys(post[0].body).every((k) => ['id', 'platform', 'identity', 'label', 'capability', 'voice'].includes(k)),
    'the add body carries no credential field', 'add body keys: ' + Object.keys(post[0].body));
  const newId = post[0].body.id;
  const painted = await page.$eval(`[data-conn-account="${newId}"] [data-conn-publish-text]`, (e) => e.textContent).catch(() => null);
  check(/^not connected$/.test((painted || '').trim()), `the new row takes the server's derived state ("${painted}")`, 'new row publish: ' + painted);
  check(srv.accounts.length === before + 1, 'the server holds the account', 'server accounts: ' + srv.accounts.length);

  // Remove: M5; a refusal rolls the row back with the server's reason.
  srv.next[`DELETE /api/desk/accounts/${newId}`] = 'a campaign still places this account';
  await page.click(`[data-conn-remove="${newId}"]`);
  await page.waitForFunction(() => /was not saved: a campaign still places this account/.test(document.body.innerText), null, { timeout: 8000 });
  check(!!(await page.$(`[data-conn-account="${newId}"]`)), 'a refused remove puts the row back and shows the server reason', 'refused remove not rolled back');
  srv.log.length = 0;
  await page.click(`[data-conn-remove="${newId}"]`);
  await settle(page, (id) => !document.querySelector(`[data-conn-account="${id}"]`), newId);
  check(calls(srv, 'DELETE', new RegExp(`^/api/desk/accounts/${newId}$`)).length === 1 && !srv.accounts.some((a) => a.id === newId),
    '✕ DELETEs M5 and the server no longer has the account', 'remove calls: ' + JSON.stringify(srv.log));

  // Read via: M4, filed under the project that uses the account.
  srv.log.length = 0;
  await page.click('[data-conn-account="ch-x-ron"] [data-readvia="api"]');
  await settle(page, () => /ch-x-ron/.test('ch-x-ron') && document.querySelector('[data-conn-account="ch-x-ron"] [data-readvia="api"]').getAttribute('aria-pressed') === 'true');
  const rv = calls(srv, 'PATCH', /^\/api\/desk\/accounts\/ch-x-ron$/);
  check(rv.length === 1 && rv[0].body.read_via === 'api' && typeof rv[0].body.project_id === 'string',
    'Read via PATCHes M4 with read_via and the project that uses the account', 'read via: ' + JSON.stringify(rv.map((r) => r.body)));
  check(calls(srv, 'PATCH', /^\/api\/desk\/presence\//).length === 0, 'it does not call the retired presence route live', 'presence route called');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 3: Where, live ─────────────────────────────────────────────────────────
async function whereLive(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhere(page);

  const none = await page.$$('.desk-v1-where [data-where-time], .desk-v1-where .desk-v1-where-time');
  check(none.length === 0, 'no time on any Where card (When owns times)', 'a time element is on Where');
  const tile = await page.$('[data-where-source-off][data-channel-id="ch-li-page"], [data-where-source][data-channel-id="ch-li-page"]');
  check(!!tile, 'the LinkedIn page is a source tile', 'no LinkedIn tile');

  // Add a column: keyboard on a source card -> PATCH the campaign's plan.
  const offId = 'ch-x-clayrune'; // an account the campaign does not place yet
  srv.log.length = 0;
  await page.focus(`[data-where-source][data-channel-id="${offId}"]`);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  const cp = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  check(cp.length === 1 && cp[0].body.plan && cp[0].body.plan.accounts.includes(offId), `adding ${offId} PATCHes the campaign plan with it in plan.accounts`, 'column add: ' + JSON.stringify({ offId, log: srv.log.map((r) => r.method + ' ' + r.path + r.search) }));

  // A message onto an account: M17.
  const famId = await page.evaluate(() => document.querySelector('[data-where-message]').dataset.familyId);
  srv.log.length = 0;
  await page.focus(`[data-where-message][data-family-id="${famId}"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 3000 });
  await page.locator('.desk-v1-addto-menu button').first().click();
  await page.waitForTimeout(250);
  const vp = calls(srv, 'POST', new RegExp(`^/api/desk/pieces/${famId}/versions$`));
  check(vp.length === 1 && typeof vp[0].body.id === 'string' && typeof vp[0].body.account_id === 'string' && !('state' in vp[0].body),
    'a message dropped on an account POSTs M17 {id, account_id}', 'version POST: ' + JSON.stringify(vp.map((r) => r.body)));
  const newV = vp[0].body;
  check((await stateOf(page, famId, newV.id)).channelId === newV.account_id, 'the board keeps the version on that account', 'version not kept');

  // Move: M18 account_id.
  const other = await page.evaluate((cur) => {
    const cols = [...document.querySelectorAll('[data-where-remove]')].map((e) => e.dataset.channelId);
    return cols.find((c) => c !== cur);
  }, newV.account_id);
  srv.log.length = 0;
  await page.focus(`[data-where-version][data-version-id="${newV.id}"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 3000 });
  const label = (await srv.accounts.find((a) => a.id === other).identity);
  await page.locator('.desk-v1-addto-menu button', { hasText: label }).first().click();
  await page.waitForTimeout(250);
  const mp = calls(srv, 'PATCH', new RegExp(`^/api/desk/pieces/${famId}/versions/${newV.id}$`));
  check(mp.length >= 1 && mp.some((r) => r.body.account_id === other) && mp.every((r) => !['approved', 'scheduled'].includes(r.body.state)),
    'moving a version PATCHes M18 {account_id}, never an approved/scheduled state', 'move PATCH: ' + JSON.stringify(mp.map((r) => r.body)));

  // Remove a column that has a pending version: plan PATCH then the version archived.
  srv.log.length = 0;
  await page.click(`[data-where-remove][data-channel-id="${other}"]`);
  await page.waitForTimeout(300);
  const planOut = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  const arch = calls(srv, 'PATCH', /\/versions\//).filter((r) => r.body.state === 'archived');
  check(planOut.length === 1 && !planOut[0].body.plan.accounts.includes(other), 'removing a column PATCHes the plan without it', 'remove plan: ' + JSON.stringify(planOut.map((r) => r.body.plan.accounts)));
  check(arch.length >= 1, `its pending version is archived through M18 (${arch.length})`, 'no archive PATCH: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  const afterV = await stateOf(page, famId, newV.id);
  check(afterV && afterV.state === 'archived', 'the board shows it archived', 'board state: ' + JSON.stringify(afterV));

  // A refused column add is rolled back with the server's text.
  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'this campaign is archived';
  await page.focus(`[data-where-source][data-channel-id="${other}"]`);
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => /was not saved: this campaign is archived/.test(document.body.innerText), null, { timeout: 8000 });
  check(!(await page.evaluate((id) => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1').plan.accounts.includes(id), other)),
    'a refused column add is rolled back with the server text', 'refused add kept the column');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 4: flag OFF ────────────────────────────────────────────────────────────
async function demoCallsNothing(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => window.DeskV1Store.demo());
  srv.log.length = 0;
  await openWhere(page);
  await page.focus('[data-where-source]');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(250);
  await page.evaluate(() => window.deskV1Nav('home'));
  await page.waitForSelector('.desk-v1-home-connections-btn', { timeout: 6000 });
  await openConnections(page);
  const live = await page.$$('[data-conn-add], [data-conn-remove], [data-conn-publish]');
  check(live.length === 0, 'flag OFF: Connections shows no Add form, Remove or Publishing line', 'live controls leaked into demo mode');
  // The read-coverage line under Read via is a pre-existing GET (S8 owns engagement); it is not an account write.
  const own = srv.log.filter((r) => !/^\/api\/desk\/engagement\/coverage\//.test(r.path));
  check(own.length === 0, 'flag OFF: Where and Connections made 0 /api/desk/* requests (bar the read-coverage GET)', 'demo mode called the server: ' + JSON.stringify(own.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: Connections reads the derived state'); await connectionsRead(browser);
  console.log('live ON: add / remove / read via'); await addRemove(browser);
  console.log('live ON: Where'); await whereLive(browser);
  console.log('live OFF: demo'); await demoCallsNothing(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — S5 Connections and Where call the account and version routes live, show the server\'s publish state and reason, never ask for a credential, roll back refusals, and demo mode calls nothing.');
