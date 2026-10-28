#!/usr/bin/env node
/**
 * Desk v1 (MC-1115) — Permissions on a SAVED account offers the Desk Read and Post rows.
 *
 * Live repro: RanLevi15 (X, an app sign-in in the vault, Post not ticked). Connections > the Post notice
 * reopened the account as type "account", which is not a picker type, so the Permissions step found no route and
 * drew only the reading section. Everything else is the real page: the real type projection
 * (mc.desk_connect.type_view), the real Connections row, reopen, Permissions, Summary and passcode prompt.
 *
 *   1. Edit settings     the notice names Edit settings in words (no second button); Edit settings reaches Permissions
 *                        with Read AND Post rows (owner "Desk permission"), beside the reading section.
 *   2. As saved          existing scopes show as they are: Read on (it is saved), Post off; nothing is sent by arriving,
 *                        Continuing, or reaching Review.
 *   3. Ticking Post      the commit draft carries Post and keeps the scopes it did not touch (the Read, the browser Read).
 *   4. Passcode          the write is the existing permissions/commit path behind the passcode prompt; cancelling sends nothing.
 *   5. Browser only      an account saved only on the sign-in route offers Read (browser) and no Post.
 *   6. Unknown route     an account with no known route draws no Desk rows and nothing breaks.
 *   7. One Read, whole   a legacy X account signed in by the app but read through the browser: ONE Read tick commits the
 *                        read scopes of BOTH routes, or its browser mentions and metrics fail the permission check.
 *   8. Profile switch    drafting another browser profile and ticking Read grants x.com on THAT profile (the one the
 *                        account will have), leaves the old profile's list alone, and reports saved only when the
 *                        new profile really covers the site.
 *
 * RUN   cd tools/smoke && node desk-v1-connect-saved-permissions.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connect_saved_permissions');
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
got = resolve.resolve('https://x.com', own_hosts=('mc.smoke.test',))
svc = got.pop('service') or registry.lookup(got['host'])
print(json.dumps({**got, **type_view.project_service(svc['id'] if svc else None)}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const PASSCODE = 'right-passcode';
const X_READ = { purpose: 'read_own', capability: 'own_posts', route_id: 'x-oauth' };
const X_POST = { purpose: 'publish', capability: 'post', route_id: 'x-oauth' };
const BROWSER_READ = { purpose: 'read_own', capability: 'own_posts', route_id: 'x-browser' };
const explicit = (id, scopes) => ({ state: 'explicit', version: 1, account_id: id, service: 'x', account_kind: 'account',
  read: scopes.some((s) => s.purpose === 'read_own'), post: scopes.some((s) => s.purpose === 'publish'), scopes, fingerprint: 'fp', updated_at: 'now' });
const NO_POST = { allowed: false, code: 'post_permission', reason: 'Post is not allowed for this account yet: tick Post on its Permissions step.' };

const account = (id, identity, extra) => ({ id, platform: 'x', identity, label: '𝕏 · ' + identity, capability: 'direct', connected: true, voice: '',
  publish: { ready: true, reason: null, secret: null, unattended_ok: true }, ...extra });
const ACCOUNTS = () => [
  account('acct-oauth', 'RanLevi15', { credentials: { oauth_profile: 'main', oauth_vault: 'oauth.x.acct-oauth' }, post_permission: NO_POST,
    browser_profile: 'main', browser_setup: { account_kind: 'account', route_id: 'x-browser', service: 'x', refs: { browser_profile: 'main', login: 'x.com' } } }),
  account('acct-browser', 'BrowserOnly', { browser_profile: 'main', browser_setup: { account_kind: 'account', route_id: 'x-browser', service: 'x', refs: { browser_profile: 'main', login: 'x.com' } } }),
  account('acct-bare', 'Bare', {}),
  account('acct-legacy', 'LegacyX', { credentials: { oauth_profile: 'main', oauth_vault: 'oauth.x.acct-legacy' },
    browser_profile: 'main', browser_setup: { account_kind: 'account', route_id: 'x-browser', service: 'x', refs: { browser_profile: 'main', login: 'x.com' } } }),
];

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, accounts: ACCOUNTS(), profiles: ['main', 'other'], putMode: 'store', puts: [],
    agentRead: { main: { enabled: true, domains: ['reddit.com'] }, other: { enabled: true, domains: [] } },
    policies: { 'acct-oauth': explicit('acct-oauth', [X_READ, BROWSER_READ]), 'acct-browser': { state: 'legacy', read: null, post: null, scopes: [], fingerprint: null } } };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  return srv;
}

async function newPage(browser, srv, width = 1400, height = 950) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname, method = req.method();
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
    if (path === '/api/secrets/vault-lock' && method === 'GET') return J({ state: 'unlocked', configured: true });
    if (path === '/api/secrets' && method === 'GET') return J({ secrets: [] });
    if (path === '/api/browser/profiles') return J({ profiles: srv.profiles.map((name) => ({ name, size_mb: 1, last_used: null, in_use_by: null })) });
    if (path === '/api/browser/agent-read') return J({ profiles: srv.agentRead });
    const put = path.match(/^\/api\/browser\/profiles\/([^/]+)\/agent-read$/);
    if (put && method === 'PUT') {
      let b = null;
      try { b = req.postDataJSON(); } catch (_) { /* none */ }
      srv.puts.push({ name: put[1], body: b });
      if (!b || b.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.putMode === 'store') srv.agentRead[put[1]] = { enabled: !!b.enabled, domains: b.domains };
      return J({ ok: true, profile: put[1], enabled: !!b.enabled, domains: b.domains });
    }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    const patch = path.match(/^\/api\/desk\/accounts\/([^/]+)$/);
    if (patch && method === 'PATCH') { const a = srv.accounts.find((x) => x.id === patch[1]); Object.assign(a, body); return J(a); }
    if (path === '/api/desk/connect/types' && method === 'POST') return J(TYPES);
    if (path === '/api/desk/connect/purposes') return J({ service: 'x', accounts: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected' }, x: { state: 'connected', app: { client_id: true }, scopes: ['tweet.read'] }, gemini: { saved: false }, openai: { saved: false } });
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) return J({ coverage: [] });
    const pv = path.match(/^\/api\/desk\/connect\/permissions\/([^/]+)$/);
    if (pv && method === 'GET') return J({ account_id: pv[1], policy: srv.policies[pv[1]] || { state: 'legacy', read: null, post: null, scopes: [], fingerprint: null } });
    if (path === '/api/desk/connect/permissions/commit' && method === 'POST') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
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
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (e) => e.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const boxes = (page) => page.$$eval('[data-cfp-box]', (bs) => bs.map((b) => b.dataset.cfpBox));
const checked = (page, id) => page.$eval(`[data-cfp-box="${id}"]`, (b) => b.checked);
const READS = ['/api/desk/connect/types', '/api/desk/connect/purposes', '/api/desk/connect/custom/connections'];   // POSTs that only read
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !READS.includes(r.path));
const commits = (srv) => srv.log.filter((r) => r.path === '/api/desk/connect/permissions/commit');
const selectTile = async (page, id) => {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 6000 });
};
const toPermissions = async (page, id, via) => {
  await selectTile(page, id);
  await page.click(`[data-conn-account="${id}"] ${via}`);
  await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 4000 });
  await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 4000 });
};
const waitPrompt = (page) => page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
const answer = async (page, code) => { await waitPrompt(page); await page.fill('input[id^="hp-passcode-"]', code); await page.click('.modal-content .btn-add'); };
const cancel = async (page) => { await waitPrompt(page); await page.click('.modal-content .btn-secondary'); };

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1-4: RanLevi15's shape, the app sign-in plus the saved browser sign-in ───────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-oauth');
    const note = await text(page, '[data-conn-account="acct-oauth"] [data-post-permission]');
    check(/Post is off\./.test(note) && /Edit/.test(note) && !(await page.$('[data-conn-account="acct-oauth"] [data-pp-open]')) && !/Open Permissions/.test(await text(page, '[data-conn-account="acct-oauth"]')),
      'the row says Post is not ticked, points at Edit settings in words and has no Open Permissions button', 'the notice or its button is wrong: ' + note);
    check((await page.$$eval('[data-conn-account="acct-oauth"] [data-cs-reopen]', (e) => e.map((x) => x.textContent.trim()))).join('|') === 'Edit', 'the account has one button into its settings, Edit settings', 'the Edit settings button is missing or doubled');
    await toPermissions(page, 'acct-oauth', '[data-cs-reopen]');
    check((await boxes(page)).join('|') === 'read|post', `Permissions offers the Desk Read and Post rows (${(await boxes(page)).join(', ')})`, 'boxes: ' + (await boxes(page)).join('|'));
    check(!!(await page.$('[data-reading-settings]')), 'the reading section is still there beside them', 'the reading section is gone');
    const owners = await page.$$eval('[data-cfp-owner]', (e) => e.map((x) => x.textContent.trim()));
    check(owners.join('|') === 'Desk permission|Desk permission', 'each row says it is a Desk permission', 'owners: ' + owners.join('|'));
    check(/Post only\. Not Reply, media or Article\./.test(await text(page, '[data-cfp-box="post"] ~ .desk-v1-cfp-text')), 'Post covers Post only', 'post coverage wrong');
    // The Desk read is saved but x.com is not on the sign-in's list yet: half-ticked, never plain "on" (the card says Reading is off).
    check(!(await checked(page, 'read')) && await page.$eval('[data-cfp-box="read"]', (b) => b.indeterminate) && !(await checked(page, 'post')) && /Reading through your sign-in is not switched on yet\./.test(await text(page, '[data-cfw-option="read"]')),
      'the saved Desk read shows half-ticked with a sentence that the sign-in is not switched on, Post off', `read=${await checked(page, 'read')} post=${await checked(page, 'post')}`);
    check(writes(srv).length === 0, 'arriving sent no write', 'a write went out: ' + JSON.stringify(writes(srv).map((r) => r.path)));
    await page.screenshot({ path: resolve(SHOT_DIR, `saved_oauth_${width}.png`) });

    // Continue without touching anything: still no write.
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw][data-cfw-step="review"]', { timeout: 4000 });
    check(writes(srv).length === 0, 'Continue to Review with nothing touched sent no write', 'Continue wrote');
    const none = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(none.permissionPending === false, 'nothing touched: no Desk permission change is pending', JSON.stringify(none));
    await page.click('[data-cfw-back]');
    await page.waitForSelector('[data-cfw][data-cfw-step="permissions"]', { timeout: 4000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 4000 });

    // Tick Post; the write is the existing commit behind the passcode prompt.
    await page.check('[data-cfp-box="post"]');
    const pending = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(pending.permissionPending === true && pending.lines.some((l) => /Desk: Post/.test(l)), 'ticking Post makes the Desk change pending', JSON.stringify(pending));
    check(writes(srv).length === 0, 'ticking sent nothing', 'ticking wrote');
    let done = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-oauth'));
    await cancel(page);
    let r = await done;
    check(r.desk.status === 'cancelled' && commits(srv).length === 0, 'cancelling the passcode prompt sends nothing', JSON.stringify(r));
    done = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-oauth'));
    await answer(page, PASSCODE);
    r = await done;
    const sent = commits(srv).pop();
    const keys = sent ? sent.body.draft.scopes.map((s) => `${s.purpose}|${s.capability}|${s.route_id}`).sort().join(',') : '';
    check(r.desk.status === 'saved' && srv.policies['acct-oauth'].post === true, 'with the passcode the account is saved with Post on', JSON.stringify(r));
    check(keys === 'publish|post|x-oauth,read_own|own_posts|x-browser,read_own|own_posts|x-oauth', 'the draft is Post plus the scopes it did not touch (the Read and the browser Read), nothing more', keys);
    check(!!sent && sent.body.draft.account_id === 'acct-oauth' && sent.body.draft.account_kind === 'account' && sent.body.draft.service === 'x', 'the draft names the account, X and its kind', JSON.stringify(sent && sent.body.draft));
    check(!pageErrors.length || realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 5: saved on the browser sign-in only ───────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await toPermissions(page, 'acct-browser', '[data-cs-reopen]');
    const b = await boxes(page);
    check(b.join('|') === 'read|post' && !(await page.locator('[data-cfp-box="read"]').isDisabled()) && (await page.locator('[data-cfp-box="post"]').isDisabled()), `a browser-only account shows the same two rows: Read works, Publish is disabled (${b.join(', ')})`, 'boxes: ' + b.join('|'));
    check(!(await page.$('[data-cfp-paid]')), 'the browser Read carries no paid-API notice', 'paid notice on browser Read');
    check(writes(srv).length === 0, 'arriving sent no write', 'a write went out');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 6: no known route ──────────────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await toPermissions(page, 'acct-bare', '[data-cs-reopen]');
    check((await boxes(page)).join('|') === 'read|post' && (await page.locator('[data-cfp-box="read"]').isDisabled()) && (await page.locator('[data-cfp-box="post"]').isDisabled()), 'an account with no known route shows the same two rows, both disabled', 'boxes: ' + (await boxes(page)).join('|'));
    check(!!(await page.$('[data-reading-settings]')), 'its reading section still draws', 'the reading section is gone');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 7: ONE Read tick commits the read scopes of every route the account reads through ─────
  {
    const srv = makeServer();
    srv.agentRead.main = { enabled: true, domains: ['x.com'] };      // the site is already on the sign-in: only the Desk write is left
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await toPermissions(page, 'acct-legacy', '[data-cs-reopen]');
    check(!(await checked(page, 'read')), 'a legacy account opens with Read off', 'Read was already on');
    await page.check('[data-cfp-box="read"]');
    const done = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-legacy'));
    await answer(page, PASSCODE);
    const r = await done;
    const sent = commits(srv).pop();
    const keys = sent ? sent.body.draft.scopes.map((s) => `${s.purpose}|${s.capability}|${s.route_id}`).sort().join(',') : '';
    const variants = TYPES.picker.flatMap((t) => t.variants);
    const capsOf = (rid) => ((variants.find((x) => x.route_id === rid) || {}).coverage || []).filter((c) => c.purpose === 'read_own' && c.account_kind === 'account' && ['documented', 'claimed'].includes(c.status)).map((c) => c.capability);
    const want = ['x-oauth', 'x-browser'].flatMap((rid) => capsOf(rid).map((c) => `read_own|${c}|${rid}`)).sort().join(',');
    check(capsOf('x-browser').length > 1 && capsOf('x-oauth').length > 0, `the fixture routes cover reads (x-oauth ${capsOf('x-oauth').join('/')}, x-browser ${capsOf('x-browser').join('/')})`, 'the fixture routes cover no reads');
    check(r.desk.status === 'saved' && keys === want, 'the one Read tick commits every read scope of the API route AND the browser route', `sent: ${keys}\nwant: ${want}`);
    check(!sent || !sent.body.draft.scopes.some((s) => s.purpose === 'publish'), 'and nothing of Post', keys);
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 8: a drafted profile change aims the site grant at the NEW profile ─────────────────────
  for (const mode of ['store', 'ignore']) {
    const srv = makeServer();
    srv.putMode = mode;
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await toPermissions(page, 'acct-browser', '[data-cs-reopen]');
    await page.fill('[data-reading-profile]', 'other');
    await page.check('[data-cfp-box="read"]');
    const done = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-browser'));
    await answer(page, PASSCODE);            // the Desk write
    await answer(page, PASSCODE);            // the site write
    const r = await done;
    const patch = srv.log.find((x) => x.method === 'PATCH');
    check(srv.puts.length === 1 && srv.puts[0].name === 'other' && srv.puts[0].body.domains.includes('x.com'), `[${mode}] the grant went to the drafted profile "other", not the loaded one`, JSON.stringify(srv.puts.map((p) => [p.name, p.body && p.body.domains])));
    check(JSON.stringify(srv.agentRead.main) === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), `[${mode}] the old profile's list is untouched`, JSON.stringify(srv.agentRead.main));
    check(!!patch && patch.body.browser_profile === 'other', `[${mode}] the account is switched to that same profile`, JSON.stringify(patch && patch.body));
    if (mode === 'store') check(r.site.status === 'saved' && r.ok === true, '[store] saved is reported because the new profile really covers x.com', JSON.stringify(r.site));
    else check(r.site.status === 'failed' && /"other" still does not allow reading x\.com/.test(r.site.error || '') && r.ok === false, '[ignore] a profile that still does not cover the site is reported failed, with the reason', JSON.stringify(r.site));
    check(realErrors(pageErrors).length === 0, `[${mode}] no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1400, 950);
  await run(browser, 390, 844);
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
