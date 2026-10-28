#!/usr/bin/env node
/**
 * Desk v1 (Ron 2026-10-28) — one button set on every saved account card: Edit settings, and a Sign in on each route row.
 *
 * Live repro: LinkedIn "Clayrune" said Needs sign-in (profile linked_ronlevy, "no saved browser profile") and nothing on the
 * card started a sign-in. The page is the real one (real type projection from mc.desk_connect, real Connections card, real
 * held sign-in module, real passcode prompt); the server and the browser pane are stubs, so no real account is touched.
 *
 *   1. Edit settings   the one button into the wizard; "Change how this is connected" and "Open Permissions" are gone.
 *   2. Browser route   Sign in sits in the Browser row (primary while it Needs sign-in) and opens the browser pane on the
 *                      platform's reviewed sign-in page with THE ACCOUNT'S OWN named profile. No wizard, no server write.
 *   3. API route (X)   Sign in sits in the API row (primary while not signed in) and runs the passcode-gated start-held for
 *                      that account; cancelling the passcode sends nothing; the held token is stored only by Save sign-in,
 *                      a second passcode-gated POST /api/desk/connect/commit that carries that account and {flow_id, claim}.
 *   4. Both routes     an account with a Browser and an API route shows one Sign in per row.
 *   5. No mechanism    an account with no profile and no API route gets no button; a platform with no reviewed sign-in page
 *                      (YouTube) is learned on the first press (never on paint), opens nothing and shows a note instead.
 *
 * RUN   cd tools/smoke && node desk-v1-account-signin.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'conn_buttons');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

// The real type projection per platform, from the real registry (what POST /api/desk/connect/types answers).
const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
out = {}
for plat in ('x', 'linkedin', 'youtube'):
    got = resolve.resolve('https://' + plat + '.com', own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    out['https://' + plat + '.com'] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const PASSCODE = 'right-passcode';
const account = (id, platform, identity, extra) => ({ id, platform, identity, label: identity, capability: 'direct', connected: true, voice: '',
  publish: { ready: false, reason: 'not signed in', secret: null, unattended_ok: null }, ...extra });
const setup = (route, profile, login) => ({ account_kind: platformKind(route), route_id: route, service: route.split('-')[0], refs: { browser_profile: profile, login } });
const platformKind = (route) => (route.startsWith('linkedin') ? 'organization' : 'account');
const ACCOUNTS = () => [
  // The brief's repro: a LinkedIn organisation whose last read hit the sign-in page.
  account('acct-li', 'linkedin', 'Clayrune', { browser_profile: 'linked_ronlevy', browser_setup: setup('linkedin-browser', 'linked_ronlevy', 'ronlevy') }),
  // X with BOTH routes: its own API sign-in (not signed in) and a saved browser profile.
  account('acct-x', 'x', 'RanLevi15', { credentials: { oauth_profile: 'x_oauth_acct_x' }, browser_profile: 'x_ron', browser_setup: setup('x-browser', 'x_ron', 'x.com') }),
  // A platform with a saved browser profile but no reviewed browser sign-in page.
  account('acct-yt', 'youtube', 'RonChannel', { capability: 'none', browser_profile: 'yt_ron', browser_setup: { account_kind: 'account', route_id: 'browser-profile', service: 'youtube', refs: { browser_profile: 'yt_ron', login: 'youtube.com' } } }),
  // No profile and no API route at all (a hand-posted account).
  account('acct-bare', 'x', 'Bare', { capability: 'manual', publish: { ready: true, reason: null, secret: null, unattended_ok: null } }),
];

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, accounts: ACCOUNTS(), flow: 'pending', commitStatus: 201 };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  return srv;
}

async function newPage(browser, srv, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskGuidePollMs = 40; });
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
    if (path === '/api/browser/profiles') return J({ profiles: [] });
    if (path === '/api/browser/agent-read') return J({ profiles: {} });
    if (path.startsWith('/api/browser/')) { srv.log.push({ method, path, body: null }); return J({ error: 'the smoke has no browser' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/types' && method === 'POST') return J(TYPES[body && body.input] || { host: '', picker: [] });
    if (path === '/api/desk/connect/purposes') return J({ service: 'x', accounts: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected' }, x: { state: 'not_connected', app: { client_id: true }, scopes: ['tweet.read'] }, gemini: { saved: false }, openai: { saved: false } });
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({ policy: { state: 'legacy', scopes: [] } });
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) {
      return J({ coverage: [{ platform: 'linkedin', via: 'pane', state: 'not_connected', reason: 'no saved browser profile' }] });
    }
    if (path === '/api/desk/connect/x/start-held' && method === 'POST') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ flow_id: 'flow-1', claim: 'claim-1', auth_url: 'https://x.com/i/oauth2/authorize?state=s', profile: 'x_oauth_acct_x', account_id: body.account_id, hold_ttl_s: 600 }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-1' && method === 'GET') return J(srv.flow === 'held' ? { status: 'held', held_ttl_s: 540 } : { status: 'pending' });
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.commitStatus !== 201) return J({ error: 'the held sign-in is no longer there' }, srv.commitStatus);
      const x = srv.accounts.find((a) => a.id === body.draft.account_id);
      x.publish = { ready: true, reason: null, secret: 'x.oauth', unattended_ok: true };
      return J({ ok: true, account_id: x.id }, 201);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  // The browser pane is a stub that records the call: the smoke opens no real pane and no real profile.
  await page.evaluate(() => { window.__panes = []; window.openBrowserPane = (...a) => { window.__panes.push(a); return Promise.resolve(); }; });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (e) => e.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const selectTile = async (page, id) => {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 6000 });
};
const panes = (page) => page.evaluate(() => window.__panes);
const only = (srv, re) => srv.log.filter((r) => re.test(r.path));
const waitPrompt = (page) => page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
const answer = async (page, code) => { await waitPrompt(page); await page.fill('input[id^="hp-passcode-"]', code); await page.click('.modal-content .btn-add'); };
const cancel = async (page) => { await waitPrompt(page); await page.click('.modal-content .btn-secondary'); };
const buttons = (page, id, sel) => page.$$eval(`[data-conn-account="${id}"] ${sel}`, (e) => e.map((x) => ({ text: x.textContent.trim(), primary: x.classList.contains('desk-v1-cf-primary'), row: (x.closest('[data-cs-facet]') || {}).dataset ? x.closest('[data-cs-facet]').dataset.csFacet : '' })));

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1, 2: LinkedIn, the repro ──────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await page.waitForSelector('[data-conn-account="acct-li"] [data-cs-facet="browser"] [data-as-signin="browser"]', { timeout: 4000 });
    const edit = await buttons(page, 'acct-li', '[data-cs-reopen]');
    check(edit.length === 1 && edit[0].text === 'Edit settings', 'the LinkedIn card has one Edit settings', JSON.stringify(edit));
    const body = await page.evaluate(() => document.body.innerText);
    check(!/Change how this is connected|Open Permissions/.test(body), 'no "Change how this is connected" and no "Open Permissions" on the screen', 'an old label survived');
    const si = await buttons(page, 'acct-li', '[data-as-signin]');
    check(si.length === 1 && si[0].text === 'Sign in' && si[0].row === 'browser', 'one Sign in, in the Browser row, and it says only "Sign in"', JSON.stringify(si));
    check(si[0].primary, 'it is primary while the row Needs sign-in', JSON.stringify(si));
    check(!(await page.$('[data-conn-account="acct-li"] [data-cs-facet="api"]')), 'LinkedIn has no API row, so no second Sign in', 'an API row on LinkedIn');
    await page.screenshot({ path: resolve(SHOT_DIR, `linkedin_${width}.png`) });
    srv.log.length = 0;
    await page.click('[data-conn-account="acct-li"] [data-as-signin="browser"]');
    await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
    const p = (await panes(page))[0];
    check(p[0] === 'https://www.linkedin.com/login' && p[3] === 'linked_ronlevy', `Sign in opens the pane on the LinkedIn sign-in page in the account's own profile (${p[0]}, ${p[3]})`, 'pane call: ' + JSON.stringify(p));
    check(!(await page.$('[data-cfw]')) && only(srv, /start-held|commit|permissions/).length === 0 && only(srv, /^\/api\/browser\//).length === 0,
      'it did not open the wizard, start an API sign-in or write anything', 'requests: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 3, 4: X, both routes ───────────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-x');
    await page.waitForSelector('[data-conn-account="acct-x"] [data-cs-facet="api"] [data-as-signin="api"]', { timeout: 4000 });
    const si = await buttons(page, 'acct-x', '[data-as-signin]');
    check(si.length === 2 && si.every((b) => b.text === 'Sign in') && si.map((b) => b.row).join() === 'browser,api', 'an account with both routes shows one Sign in per row', JSON.stringify(si));
    check(si.find((b) => b.row === 'api').primary && !si.find((b) => b.row === 'browser').primary, 'the API row (not signed in) is primary and the Browser row (saved, unchecked) is plain', JSON.stringify(si));
    check((await buttons(page, 'acct-x', '[data-cs-reopen]')).length === 1, 'and still one Edit settings', 'Edit settings doubled');
    await page.screenshot({ path: resolve(SHOT_DIR, `x_${width}.png`) });

    // Browser row: the X sign-in page, the account's browser profile.
    await page.click('[data-conn-account="acct-x"] [data-as-signin="browser"]');
    await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
    const bp = (await panes(page))[0];
    check(bp[0] === 'https://x.com/i/flow/login' && bp[3] === 'x_ron', `the Browser row opens X's sign-in page in the account's browser profile (${bp[0]}, ${bp[3]})`, 'pane call: ' + JSON.stringify(bp));

    // API row: the passcode-gated held sign-in. Cancelling at the passcode sends nothing.
    srv.log.length = 0;
    await page.click('[data-conn-account="acct-x"] [data-as-signin="api"]');
    await cancel(page);
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-signin="api"]', { timeout: 4000 });
    check(only(srv, /start-held/).length === 0, 'cancelling the passcode prompt sends no start-held request', 'start-held went out');
    await page.click('[data-conn-account="acct-x"] [data-as-signin="api"]');
    await answer(page, PASSCODE);
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-state="waiting"]', { timeout: 4000 });
    const start = only(srv, /start-held/);
    check(start.length === 1 && start[0].body.account_id === 'acct-x' && start[0].body.passcode === PASSCODE && Object.keys(start[0].body.hold || {}).length === 0,
      'the API row posts start-held for THIS account behind the passcode, typing no app details', JSON.stringify(start.map((r) => r.body)));
    await page.waitForFunction(() => window.__panes.length === 2, null, { timeout: 4000 });
    const ap = (await panes(page))[1];
    check(ap[0].startsWith('https://x.com/i/oauth2/authorize') && ap[3] === 'x_oauth_acct_x', `the sign-in page opened in the profile the server named (${ap[3]})`, 'pane call: ' + JSON.stringify(ap));
    check(only(srv, /commit/).length === 0, 'nothing is saved while waiting', 'a commit went out');

    srv.flow = 'held';
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-state="held"]', { timeout: 6000 });
    check(!!(await page.$('[data-conn-account="acct-x"] [data-as-save]')) && only(srv, /commit/).length === 0, 'once signed in it is held and Save sign-in is offered; still nothing saved', 'no Save sign-in, or a commit went out');
    await page.screenshot({ path: resolve(SHOT_DIR, `x_held_${width}.png`) });
    await page.click('[data-conn-account="acct-x"] [data-as-save]');
    await answer(page, PASSCODE);
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-msg="api"]', { timeout: 6000 });
    const commit = only(srv, /^\/api\/desk\/connect\/commit$/);
    const d = commit[0] && commit[0].body.draft;
    check(commit.length === 1 && commit[0].body.passcode === PASSCODE && d && d.account_id === 'acct-x' && d.method === 'oauth' && d.url === 'https://x.com'
        && d.held && d.held.flow_id === 'flow-1' && d.held.claim === 'claim-1' && Object.keys(d.fields || {}).length === 0,
      'Save sign-in is the passcode-gated commit for this account with {flow_id, claim} and no typed details', JSON.stringify(commit.map((r) => r.body)));
    check(/sign-in is saved/.test(await text(page, '[data-conn-account="acct-x"] [data-as-msg="api"]')) && await page.$eval('[data-conn-account="acct-x"] [data-cs-facet="api"]', (e) => e.dataset.state) === 'ok',
      'the card then reads API signed in, from the server\'s fresh account row', 'the card did not update');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 5: no sign-in mechanism ────────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    srv.log.length = 0;
    await selectTile(page, 'acct-yt');
    await page.waitForSelector('[data-conn-account="acct-yt"] [data-as-signin="browser"]', { timeout: 4000 });
    check(only(srv, /connect\/types/).length === 0, 'opening a card looks nothing up: the sign-in page is read only when Sign in is pressed', 'a types request went out on paint');
    await page.click('[data-conn-account="acct-yt"] [data-as-signin="browser"]');
    await page.waitForSelector('[data-conn-account="acct-yt"] [data-as-none]', { timeout: 4000 });
    check((await buttons(page, 'acct-yt', '[data-as-signin]')).length === 0 && (await panes(page)).length === 0, 'a platform with no reviewed sign-in page (YouTube) opens nothing and its button gives way to a note', 'YouTube kept a Sign in or opened a pane');
    check((await buttons(page, 'acct-yt', '[data-cs-reopen]')).length === 1, 'it still has Edit settings', 'no Edit settings on YouTube');
    await selectTile(page, 'acct-bare');
    const bare = [(await buttons(page, 'acct-bare', '[data-as-signin]')).length, (await buttons(page, 'acct-bare', '[data-cs-reopen]')).length];
    check(bare.join() === '0,1', 'an account with no route has no Sign in and one Edit settings', 'the bare account has the wrong buttons (sign-in, edit): ' + bare.join());
    check((await panes(page)).length === 0, 'no pane was opened by looking at them', 'a pane opened on its own');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1440, 950);
  await run(browser, 390, 844);
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nPASS — Edit settings and Sign in are one button set per account; Sign in starts the real sign-in for its own route.');
