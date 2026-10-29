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
 *                      that account; cancelling the passcode sends nothing; the server saves a confirmed match automatically; lookup failures retain Save sign-in,
 *                      a fallback passcode-gated POST /api/desk/connect/commit that carries that account and {flow_id, claim}.
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
const SHOT_DIR_NS = resolve(REPO_ROOT, '_scratch', 'needs_signin');
mkdirSync(SHOT_DIR_NS, { recursive: true });
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
  const srv = { log: [], fx, accounts: ACCOUNTS(), services: [], flow: 'pending', commitStatus: 201 };
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
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/types' && method === 'POST') return J(TYPES[body && body.input] || { host: '', picker: [] });
    if (path === '/api/desk/connect/purposes') return J({ service: 'x', accounts: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected' }, x: { state: 'not_connected', app: { client_id: true }, scopes: ['tweet.read'] }, gemini: { saved: false }, openai: { saved: false } });
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({ policy: { state: 'legacy', scopes: [] } });
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) {
      return J({ coverage: [{ platform: 'linkedin', via: 'pane', state: 'not_connected', reason: 'no saved browser profile' }, {platform:'x',via:'pane',state:'not_connected',reason:'the last read hit sign-in'}, {platform:'youtube',via:'pane',state:'not_connected',reason:'the last read hit sign-in'}] });
    }
    if (path === '/api/desk/connect/x/start-held' && method === 'POST') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ flow_id: 'flow-1', claim: 'claim-1', auth_url: 'https://x.com/i/oauth2/authorize?state=s', profile: 'x_oauth_acct_x', account_id: body.account_id, hold_ttl_s: 600 }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-1' && method === 'GET') {
      if (srv.flow === 'done') {
        srv.accounts.find(a => a.id === 'acct-x').publish = {ready:true,reason:null,secret:'x.oauth',unattended_ok:true};
        return J({status:'done',message:'The X sign-in is saved.'});
      }
      if (srv.flow === 'mismatch') return J({status:'held',held_ttl_s:540,save_allowed:false,message:'You signed in as @other_user, but this account is @RanLevi15. Nothing was saved. Discard this sign-in and sign in to the correct account.'});
      if (srv.flow === 'error') return J({status:'error',message:'The X sign-in failed. The service could not finish signing in. Sign in again to retry.'});
      return J(srv.flow === 'held' ? {status:'held',held_ttl_s:540,save_allowed:true,message:'The X identity could not be confirmed. Nothing was saved. You can save this sign-in manually or discard it.'} : {status:'pending'});
    }
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
const openDetails = (page, id) => page.evaluate((i) => { const d = document.querySelector('[data-conn-account="' + i + '"] [data-connection-details]'); if (d) d.open = true; }, id);
const panes = (page) => page.evaluate(() => window.__panes);
const only = (srv, re) => srv.log.filter((r) => re.test(r.path));
const waitPrompt = (page) => page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
const answer = async (page, code) => { await waitPrompt(page); await page.fill('input[id^="hp-passcode-"]', code); await page.click('.modal-content .btn-add'); };
const cancel = async (page) => { await waitPrompt(page); await page.click('.modal-content .btn-secondary'); };
const buttons = (page, id, sel) => page.$$eval(`[data-conn-account="${id}"] ${sel}`, (e) => e.map((x) => ({ text: x.textContent.trim(), primary: x.classList.contains('desk-v1-cf-primary'), row: (x.closest('[data-cs-facet]') || {}).dataset ? x.closest('[data-cs-facet]').dataset.csFacet : '' })));

// The state line must never participate in button sizing/wrapping, at either viewport.
async function layout(page, width, state, id = 'acct-x') {
  await page.locator(`[data-conn-account="${id}"] [data-connection-actions]`).scrollIntoViewIfNeeded();
  const info = await page.$eval(`[data-conn-account="${id}"]`, el => {
    const row=el.querySelector('[data-connection-actions]');
    const buttons=[...row.querySelectorAll('button')].filter(b=>b.getBoundingClientRect().width);
    const rects=buttons.map(b=>b.getBoundingClientRect());
    const msgs=[...el.querySelectorAll('[data-account-signin-messages] > *')];
    const rr=row.getBoundingClientRect();
    return {tops:rects.map(r=>Math.round(r.top)), messagesAbove:msgs.every(m=>m.getBoundingClientRect().bottom<=rr.top+1),
      inline:!!row.querySelector('.desk-v1-cf-msg,[data-as-none]'),
      pageOverflow:document.documentElement.scrollWidth>innerWidth,
      rowFits:rr.width<=el.getBoundingClientRect().width+1};
  });
  check(new Set(info.tops).size===1 && info.messagesAbove && !info.inline && info.rowFits && !info.pageOverflow,
    `${width}px ${state}: full-width messages above one button row, no page overflow`,JSON.stringify(info));
  if (width===390) {
    const end=await page.$eval(`[data-conn-account="${id}"] [data-connection-actions]`,row=>{
      row.scrollLeft=row.scrollWidth;
      const last=[...row.querySelectorAll('button')].at(-1).getBoundingClientRect();
      const box=row.getBoundingClientRect();
      return last.right<=box.right+1 && last.left>=box.left;
    });
    check(end,`${state}: scrolling the phone action row exposes Remove`);
    await page.screenshot({path:resolve(SHOT_DIR,`${id}_${state.replaceAll(' ','_')}_actions_end_${width}.png`)});
    await page.$eval(`[data-conn-account="${id}"] [data-connection-actions]`,row=>{row.scrollLeft=0;});
  }

}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1, 2: LinkedIn, the repro ──────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await page.waitForSelector('[data-conn-account="acct-li"] [data-cs-facet="browser"] [data-as-signin="browser"]', { timeout: 4000 });
    const edit = await buttons(page, 'acct-li', '[data-cs-reopen]');
    check(edit.length === 1 && edit[0].text === 'Edit', 'the LinkedIn card has one Edit settings', JSON.stringify(edit));
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
    check(!(await page.$('[data-cfw]')) && only(srv, /start-held|commit|permissions/).every(r=>r.method==='GET') && only(srv, /^\/api\/browser\//).length === 0,
      'it did not open the wizard, start an API sign-in or write anything', 'requests: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
    await page.evaluate(()=>{window.openBrowserPane=()=>Promise.reject(new Error('scripted pane failure'));});
    await page.click('[data-conn-account="acct-li"] [data-as-signin="browser"]');
    await page.waitForSelector('[data-conn-account="acct-li"] [data-as-msg="browser"]');
    await layout(page,width,'browser error','acct-li');
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
    check(si.every(b=>b.primary), 'the API row (not signed in) is primary and the Browser row (saved, unchecked) is plain', JSON.stringify(si));
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
    check(start.length === 1 && start[0].body.account_id === 'acct-x' && start[0].body.auto_save === true && start[0].body.passcode === PASSCODE && Object.keys(start[0].body.hold || {}).length === 0,
      'the API row posts start-held for THIS account behind the passcode, typing no app details', JSON.stringify(start.map((r) => r.body)));
    await page.waitForFunction(() => window.__panes.length === 2, null, { timeout: 4000 });
    const ap = (await panes(page))[1];
    check(ap[0].startsWith('https://x.com/i/oauth2/authorize') && ap[3] === 'x_oauth_acct_x', `the sign-in page opened in the profile the server named (${ap[3]})`, 'pane call: ' + JSON.stringify(ap));
    check(only(srv, /commit/).length === 0, 'nothing is saved while waiting', 'a commit went out');

    await layout(page, width, 'waiting');
    srv.flow = 'held';
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-state="held"]', { timeout: 6000 });
    check(!!(await page.$('[data-conn-account="acct-x"] [data-as-save]')) && only(srv, /commit/).length === 0, 'identity lookup failure keeps it held with manual Save; no commit from polling', 'no Save sign-in, or a commit went out');
    await layout(page, width, 'held');
    await page.screenshot({ path: resolve(SHOT_DIR, `x_held_${width}.png`) });
    srv.commitStatus=500;
    await page.click('[data-conn-account="acct-x"] [data-as-save]');
    await answer(page,PASSCODE);
    await page.waitForSelector('[data-conn-account="acct-x"] [data-as-msg="api"][data-cf-msg="error"]');
    await layout(page,width,'manual save error');
    await page.screenshot({path:resolve(SHOT_DIR,`x_save_error_${width}.png`)});
    check(!!(await page.$('[data-as-save]')),'manual Save failure keeps its retry button and held token');
    srv.commitStatus=201; srv.log.length=0;
    await page.click('[data-conn-account="acct-x"] [data-as-save]');
    await answer(page, PASSCODE);
    await page.waitForSelector('[data-conn-account="acct-x"] [data-cs-facet="api"][data-state="ok"]', {state:'attached',timeout:6000});
    const commit = only(srv, /^\/api\/desk\/connect\/commit$/);
    const d = commit[0] && commit[0].body.draft;
    check(commit.length === 1 && commit[0].body.passcode === PASSCODE && d && d.account_id === 'acct-x' && d.method === 'oauth' && d.url === 'https://x.com'
        && d.held && d.held.flow_id === 'flow-1' && d.held.claim === 'claim-1' && Object.keys(d.fields || {}).length === 0,
      'Save sign-in is the passcode-gated commit for this account with {flow_id, claim} and no typed details', JSON.stringify(commit.map((r) => r.body)));
    check(await page.$eval('[data-conn-account="acct-x"] [data-cs-facet="api"]', (e) => e.dataset.state) === 'ok',
      'manual fallback re-reads the saved API account row', 'the card did not update');
    await layout(page, width, 'saved');
    await page.screenshot({ path: resolve(SHOT_DIR, `x_manual_saved_${width}.png`) });
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // Server auto-save, known mismatch, and callback failure: no second passcode or client commit.
  for (const state of ['done','mismatch','error']) {
    const srv=makeServer();
    const {ctx,page,pageErrors}=await newPage(browser,srv,width,height);
    await selectTile(page,'acct-x');
    await page.click('[data-conn-account="acct-x"] [data-as-signin="api"]');
    await answer(page,PASSCODE);
    await page.waitForSelector('[data-as-state="waiting"]');
    srv.flow=state;
    await page.waitForSelector(state==='done'?'[data-as-msg="api"]':state==='mismatch'?'[data-as-state="held"]':'[data-as-state="failed"]');
    if(state==='done') {
      check((await text(page,'[data-as-msg="api"]'))==='The X sign-in is saved.' && !(await page.$('[data-as-save]')),
        'confirmed card sign-in reports saved without Save sign-in');
      check(only(srv,/^\/api\/desk\/accounts$/).length>=1,'auto-save re-reads the account row');
    } else if(state==='mismatch') {
      check(!(await page.$('[data-as-save]')) && !!(await page.$('[data-cfh-cancel]')),
        'known wrong handle offers Discard only');
    }
    check(only(srv,/commit/).length===0 && !(await page.$('input[id^="hp-passcode-"]')),
      `${state}: polling sends no commit and opens no second passcode prompt`);
    await layout(page,width,state);
    await page.screenshot({path:resolve(SHOT_DIR,`x_${state==='done'?'saved':state}_${width}.png`)});
    check(realErrors(pageErrors).length===0,'no page errors in '+state,JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // Wizard contract: absent account id does not request auto-save and remains held for Review's Save.
  {
    const srv=makeServer();
    const {ctx,page}=await newPage(browser,srv,width,height);
    await page.evaluate(()=>{
      window.DeskV1ConnectHeld.begin('x','oauth','X',{api:window.DeskV1Store.api,repaint:()=>{}});
      window.DeskV1ConnectHeld.start(document.body);
    });
    await answer(page,PASSCODE);
    srv.flow='held';
    await page.waitForFunction(()=>window.DeskV1ConnectHeld.state()?.phase==='held');
    const request=only(srv,/start-held/)[0];
    check(!request.body.account_id && !request.body.auto_save && only(srv,/commit/).length===0,
      'wizard starts without auto-save or an existing account and retains its explicit Save');
    check(await page.evaluate(()=>!!window.DeskV1ConnectHeld.draft() && /Save stores it/.test(window.DeskV1ConnectHeld.reviewRowHTML())),
      'wizard Review still holds the claim for Save');
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

  // ── 6: the "Needs sign-in" word is a second way into the SAME sign-in ──────────
  {
    const word = (page, id, facet) => page.$eval(`[data-conn-account="${id}"] [data-cs-facet="${facet}"][data-state] [data-cs-word]`, (e) => ({ tag: e.tagName, text: e.textContent.trim(), aria: e.getAttribute('aria-label') }));
    const head = (page, id) => page.$eval(`[data-conn-account="${id}"] .desk-v1-conn-head [data-conn-status]`, (e) => ({ tag: e.tagName, text: e.textContent.trim(), aria: e.getAttribute('aria-label') }));

    // Browser facet: card word, route word and the Sign in button open the same pane call.
    const calls = {};
    for (const via of ['button', 'route', 'card']) {
      const srv = makeServer();
      const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
      await selectTile(page, 'acct-li');
      await page.waitForSelector('[data-conn-account="acct-li"] [data-as-signin="browser"]');
      if (via === 'route') {
        await page.click('[data-conn-account="acct-li"] [data-connection-details] > summary');
        const w = await word(page, 'acct-li', 'browser');
        check(w.tag === 'BUTTON' && w.text === 'Needs sign-in' && w.aria === 'Sign in to LinkedIn', 'LinkedIn Browser row: "Needs sign-in" is a button labelled "Sign in to LinkedIn"', JSON.stringify(w));
        await page.screenshot({ path: resolve(SHOT_DIR_NS, `linkedin_word_${width}.png`) });
      }
      if (via === 'card') {
        const h = await head(page, 'acct-li');
        check(h.tag === 'BUTTON' && h.text === 'Needs sign-in' && h.aria === 'Sign in to LinkedIn', 'LinkedIn card head: "Needs sign-in" is a button labelled "Sign in to LinkedIn"', JSON.stringify(h));
      }
      srv.log.length = 0;
      const sel = via === 'button' ? '[data-conn-account="acct-li"] [data-as-signin="browser"]' : via === 'route' ? '[data-conn-account="acct-li"] [data-cs-facet="browser"] button[data-cs-word]' : '[data-conn-account="acct-li"] .desk-v1-conn-head button[data-conn-status]';
      if (via === 'card') await page.locator(sel).press('Enter');   // the card word by keyboard
      else await page.click(sel);
      await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
      calls[via] = (await panes(page))[0];
      check(!(await page.$('[data-cfw]')) && only(srv, /start-held|commit/).length === 0 && only(srv, /^\/api\/browser\//).length === 0, `browser ${via}: no wizard, no API sign-in, no write`, JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
      check(realErrors(pageErrors).length === 0, `browser ${via}: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
      await ctx.close();
    }
    check(JSON.stringify(calls.route) === JSON.stringify(calls.button) && JSON.stringify(calls.card) === JSON.stringify(calls.button) && calls.button[3] === 'linked_ronlevy',
      'the route word and the card word open exactly what the Sign in button opens (same page, same profile)', JSON.stringify(calls));

    // API facet (X, App row): the word posts the same passcode-gated start-held as the Sign in button.
    const starts = {};
    for (const via of ['button', 'route']) {
      const srv = makeServer();
      const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
      await selectTile(page, 'acct-x');
      await page.waitForSelector('[data-conn-account="acct-x"] [data-cs-facet="api"] [data-as-signin="api"]');
      const rows = [await word(page, 'acct-x', 'browser'), await word(page, 'acct-x', 'api')];
      check(rows.every((w) => w.tag === 'BUTTON' && w.text === 'Needs sign-in') && rows[1].aria === 'Sign in to X (app)' && rows[0].aria === 'Sign in to X', 'X with both routes: each Needs sign-in word is its own button', JSON.stringify(rows));
      const h = await head(page, 'acct-x');
      check(h.tag === 'BUTTON', 'X card head is a button too', JSON.stringify(h));
      if (via === 'route') { await page.click('[data-conn-account="acct-x"] [data-connection-details] > summary'); await page.screenshot({ path: resolve(SHOT_DIR_NS, `x_words_${width}.png`) }); }
      srv.log.length = 0;
      await openDetails(page, 'acct-x'); await page.click(via === 'button' ? '[data-conn-account="acct-x"] [data-as-signin="api"]' : '[data-conn-account="acct-x"] [data-cs-facet="api"] button[data-cs-word]');
      await cancel(page);
      check(only(srv, /start-held/).length === 0, `api ${via}: cancelling the passcode sends nothing`);
      await openDetails(page, 'acct-x'); await page.click(via === 'button' ? '[data-conn-account="acct-x"] [data-as-signin="api"]' : '[data-conn-account="acct-x"] [data-cs-facet="api"] button[data-cs-word]');
      await answer(page, PASSCODE);
      await page.waitForSelector('[data-conn-account="acct-x"] [data-as-state="waiting"]', { timeout: 4000 });
      starts[via] = only(srv, /start-held/).map((r) => r.body);
      check(only(srv, /commit/).length === 0 && (await panes(page)).length >= 1 && (await panes(page)).every((p) => p[0] !== 'https://x.com/i/flow/login'), `api ${via}: waiting state shown, the browser route was not touched`);
      if (via === 'route') {
        const waiting = await word(page, 'acct-x', 'api');
        check(waiting.tag === 'SPAN', 'while the sign-in is waiting the App word is plain text (no second start)', JSON.stringify(waiting));
      }
      check(realErrors(pageErrors).length === 0, `api ${via}: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
      await ctx.close();
    }
    check(starts.button.length === 1 && JSON.stringify(starts.route) === JSON.stringify(starts.button) && starts.route[0].account_id === 'acct-x' && starts.route[0].passcode === PASSCODE,
      'the App word posts the same start-held request as the Sign in button', JSON.stringify(starts));

    // Not a sign-in: Ready, a locked vault, and a platform with no sign-in page stay plain text.
    {
      const srv = makeServer();
      srv.accounts.find((a) => a.id === 'acct-x').publish = { ready: false, vault_locked: true, reason: 'vault locked', secret: null, unattended_ok: null };
      const { ctx, page } = await newPage(browser, srv, width, height);
      await selectTile(page, 'acct-x');
      const lockedApi = await word(page, 'acct-x', 'api');
      check(lockedApi.tag === 'SPAN' && lockedApi.text === 'Not working', 'locked vault: the App word keeps its own wording and is not a button', JSON.stringify(lockedApi));
      const lockedHead = await head(page, 'acct-x');
      check(lockedHead.tag === 'SPAN' && lockedHead.text === 'Not working', 'locked vault: the card word is not a button', JSON.stringify(lockedHead));
      await selectTile(page, 'acct-bare');
      const ready = await head(page, 'acct-bare');
      check(ready.tag === 'SPAN' && ready.text === 'Ready', 'Ready is plain text, not a button', JSON.stringify(ready));
      await selectTile(page, 'acct-yt');
      await page.click('[data-conn-account="acct-yt"] [data-as-signin="browser"]');
      await page.waitForSelector('[data-conn-account="acct-yt"] [data-as-none]');
      const yt = await head(page, 'acct-yt'), ytRow = await word(page, 'acct-yt', 'browser');
      check(yt.tag === 'SPAN' && ytRow.tag === 'SPAN', 'a platform with no sign-in page: the word gives way to text, like the button', JSON.stringify([yt, ytRow]));
      await ctx.close();
    }
    {
      const srv = makeServer();
      srv.accounts.find((a) => a.id === 'acct-x').publish = { ready: true, reason: null, secret: 'x.oauth', unattended_ok: true };
      srv.accounts.find((a) => a.id === 'acct-x').browser_setup.status = { state: 'verified' };
      const { ctx, page } = await newPage(browser, srv, width, height);
      await selectTile(page, 'acct-x');
      const both = [await word(page, 'acct-x', 'browser'), await word(page, 'acct-x', 'api'), await head(page, 'acct-x')];
      check(both.every((w) => w.tag === 'SPAN' && w.text === 'Ready'), 'X with both routes Ready: no word is a button', JSON.stringify(both));
      await ctx.close();
    }
  }

  // ── 7: a saved browser-profile SERVICE (live: www.instagram.com) says Needs sign-in too ─────────
  // Live repro 2026-10-29: after 221cbbfe the only "Needs sign-in" on the Desk was this tile and its panel head, both
  // plain text: the word was wired for account cards only. The panel head word now opens the reviewed sign-in page the
  // service's own check reported, in the service's own profile; a service with no such page keeps a plain word.
  {
    const svc = (id, name, status) => ({ id, name, kind: 'browser_profile', link: 'https://' + name, publish: false, credential: { name: '', in_vault: null },
      browser_connection: { connection_id: id, browser_profile: 'main', name, read: false, post: false, executes: false, service: null, signin_method: 'other', url: 'https://' + name, status } });
    const needs = { state: 'sign_in_required', label: 'Sign-in required', needs_signin: true, verifiable: true, signin_url: 'https://www.instagram.com/accounts/login/', browser_profile: 'main' };
    const svcHead = (page, id) => page.$eval(`[data-conn-service="${id}"] .desk-v1-conn-head [data-conn-status]`, (e) => ({ tag: e.tagName, text: e.textContent.trim(), aria: e.getAttribute('aria-label') }));
    const pick = async (page, id) => { await page.click(`[data-conn-tile="service:${id}"]`); await page.waitForSelector(`[data-conn-detail="service:${id}"] [data-conn-service="${id}"]`, { timeout: 6000 }); };
    {
      const srv = makeServer();
      srv.services = [svc('svc-ig', 'www.instagram.com', needs)];
      const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
      await pick(page, 'svc-ig');
      const h = await svcHead(page, 'svc-ig');
      check(h.tag === 'BUTTON' && h.text === 'Needs sign-in' && h.aria === 'Sign in to www.instagram.com', 'saved service: "Needs sign-in" in the panel head is a button labelled "Sign in to www.instagram.com"', JSON.stringify(h));
      await page.screenshot({ path: resolve(SHOT_DIR_NS, `service_word_${width}.png`) });
      srv.log.length = 0;
      await page.locator('[data-conn-service="svc-ig"] .desk-v1-conn-head button[data-conn-status]').press('Enter');
      await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
      const call = (await panes(page))[0];
      check(call[0] === 'https://www.instagram.com/accounts/login/' && call[3] === 'main', "the word opens the service's reported sign-in page in its own profile", JSON.stringify(call));
      check(!(await page.$('[data-cfw]')) && srv.log.filter((r) => r.method !== 'GET').length === 0 && only(srv, /^\/api\/browser\//).length === 0, 'no wizard, no write, no direct browser API call', JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
      check(realErrors(pageErrors).length === 0, 'saved service: no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
      await ctx.close();
    }
    {
      // No reported sign-in page, Ready, and Not checked yet: plain text, as before.
      const srv = makeServer();
      srv.services = [svc('svc-nopage', 'example.com', { ...needs, signin_url: undefined }), svc('svc-ok', 'www.reddit.com', { state: 'verified', label: 'Verified', verifiable: true }), svc('svc-new', 'www.tiktok.com', { state: 'not_checked', label: 'Not checked', verifiable: false })];
      const { ctx, page } = await newPage(browser, srv, width, height);
      const seen = {};
      for (const id of ['svc-nopage', 'svc-ok', 'svc-new']) { await pick(page, id); seen[id] = await svcHead(page, id); }
      check(seen['svc-nopage'].tag === 'SPAN' && seen['svc-nopage'].text === 'Needs sign-in', 'saved service with no reported sign-in page: the word stays plain text', JSON.stringify(seen['svc-nopage']));
      check(seen['svc-ok'].tag === 'SPAN' && seen['svc-ok'].text === 'Ready' && seen['svc-new'].tag === 'SPAN', 'Ready and Not checked yet services stay plain text', JSON.stringify([seen['svc-ok'], seen['svc-new']]));
      await ctx.close();
    }
  }

  // ── 8: the status PILL on the grid tile starts the sign-in too (Dave/Ron 2026-10-29: option C) ───────
  // One click on a Needs sign-in pill selects its tile AND starts the tile's sign-in (the same target the panel head word uses).
  // The tile is a <button>, so the pill is a span role=button; a Ready pill, or one with nothing to start, only selects.
  {
    const svc = (id, name, status) => ({ id, name, kind: 'browser_profile', link: 'https://' + name, publish: false, credential: { name: '', in_vault: null },
      browser_connection: { connection_id: id, browser_profile: 'main', name, read: false, post: false, executes: false, service: null, signin_method: 'other', url: 'https://' + name, status } });
    const needs = { state: 'sign_in_required', label: 'Sign-in required', needs_signin: true, verifiable: true, signin_url: 'https://www.instagram.com/accounts/login/', browser_profile: 'main' };
    // The pill of a tile: the action button laid over the tile when it starts a sign-in, else the tile's own plain pill.
    // `nested` = interactive elements INSIDE the tile button (must be 0); d* = the action pill's box against the tile's own (hidden) pill.
    const pill = (page, id) => page.evaluate((id) => {
      const act = document.querySelector(`[data-conn-tile-action="${CSS.escape(id)}"]`), tile = document.querySelector(`[data-conn-tile="${CSS.escape(id)}"]`);
      const ghost = tile.querySelector('[data-conn-tile-status]'), e = act || ghost;
      const r = e.getBoundingClientRect(), g = ghost.getBoundingClientRect(), t = tile.getBoundingClientRect(), look = (n) => getComputedStyle(n).backgroundColor + '|' + getComputedStyle(n).color;
      return { tag: e.tagName, text: e.textContent.trim(), action: !!act, aria: e.getAttribute('aria-label'), tabindex: e.tabIndex, ghostVisible: getComputedStyle(ghost).visibility,
        nested: tile.querySelectorAll('button, a, [role=button], [tabindex]').length, insideTile: !!e.closest('[data-conn-tile]'),
        h: Math.round(r.height), w: Math.round(r.width), dx: Math.round(r.left - g.left), dy: Math.round(r.top - g.top), dw: Math.round(r.width - g.width), dh: Math.round(r.height - g.height),
        within: r.left >= t.left && r.right <= t.right && r.top >= t.top && r.bottom <= t.bottom, sameLook: look(e) === look(ghost) };
    }, id);
    const selected = (page, id) => page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed');
    const nPanes = (page) => page.evaluate(() => window.__panes.length);
    const sel = (id) => `[data-conn-tile-action="${id}"]`;
    const plainSel = (id) => `[data-conn-tile="${id}"] [data-conn-tile-status]`;
    // The action pill is a real <button> OUTSIDE the tile button, exactly over the tile's own pill (which stays only to size the tile).
    const isActionPill = (p) => p.action && p.tag === 'BUTTON' && !p.insideTile && p.nested === 0 && p.tabindex === 0 && p.text === 'Needs sign-in' && p.ghostVisible === 'hidden';
    const overlaps = (p) => Math.abs(p.dx) <= 1 && Math.abs(p.dy) <= 1 && Math.abs(p.dw) <= 1 && Math.abs(p.dh) <= 1 && p.within && p.sameLook;

    // Saved service: mouse, Enter, Space.
    {
      const srv = makeServer();
      srv.services = [svc('svc-ig', 'www.instagram.com', needs), svc('svc-ok', 'www.reddit.com', { state: 'verified', label: 'Verified', verifiable: true }), svc('svc-nopage', 'example.com', { ...needs, signin_url: undefined })];
      const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
      await page.waitForSelector('[data-conn-tile="service:svc-nopage"]', { timeout: 6000 });
      const p = await pill(page, 'service:svc-ig'), ready = await pill(page, 'service:svc-ok'), nopage = await pill(page, 'service:svc-nopage');
      check(isActionPill(p) && p.aria === 'Sign in to www.instagram.com', 'service tile: the Needs sign-in pill is a real <button> beside the tile button (nothing interactive inside the tile)', JSON.stringify(p));
      check(overlaps(p), 'the action pill sits exactly over the tile\'s own pill (same box, same colours)', JSON.stringify(p));
      check(p.h === ready.h && ready.action === false && ready.tag === 'SPAN', 'the action pill has the same height as a plain pill (hit area unchanged)', JSON.stringify([p, ready]));
      check(!ready.action && !nopage.action && nopage.tag === 'SPAN' && nopage.text === 'Needs sign-in' && ready.nested === 0 && nopage.nested === 0, 'Ready, and Needs sign-in with no known sign-in page, are plain pills (not clickable-looking, not focusable)', JSON.stringify([ready, nopage]));
      const looks = await page.evaluate(() => ({ actions: document.querySelectorAll('[data-conn-tile-action]').length, actionLine: getComputedStyle(document.querySelector('[data-conn-tile-action]')).textDecorationLine,
        plain: [...document.querySelectorAll('[data-conn-tile-status]:not(.desk-v1-conn-pill-ghost)')].map((e) => getComputedStyle(e).textDecorationLine) }));
      check(looks.actions >= 1 && looks.actionLine.includes('underline') && looks.plain.length >= 2 && looks.plain.every((l) => l === 'none'), 'only the startable pill is underlined; the plain pills look inert', JSON.stringify(looks));
      await page.screenshot({ path: resolve(SHOT_DIR_NS, `pill_${width}.png`) });
      srv.log.length = 0;
      check((await selected(page, 'service:svc-ig')) === 'false' && (await nPanes(page)) === 0, 'before the click: nothing selected, no pane');
      await page.click(sel('service:svc-ig'));
      await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
      const call = (await panes(page))[0];
      check((await selected(page, 'service:svc-ig')) === 'true' && !!(await page.$('[data-conn-detail="service:svc-ig"]')), 'one click: the tile is selected and its panel is open');
      check(call[0] === 'https://www.instagram.com/accounts/login/' && call[3] === 'main' && (await nPanes(page)) === 1, 'one click: exactly one openBrowserPane call, the service\'s sign-in page in its own profile', JSON.stringify(call));
      check(srv.log.filter((r) => r.method !== 'GET').length === 0 && !(await page.$('[data-cfw]')), 'no write and no wizard');
      await page.click(sel('service:svc-ig'));
      await page.waitForFunction(() => window.__panes.length === 2, null, { timeout: 4000 });
      check((await selected(page, 'service:svc-ig')) === 'true', 'clicking the pill of the selected tile keeps it selected (never closes it) and starts again');
      for (const key of ['Enter', ' ']) {
        const before = await nPanes(page);
        await page.focus(sel('service:svc-ig'));
        await page.keyboard.press(key === ' ' ? 'Space' : key);
        await page.waitForFunction((n) => window.__panes.length === n + 1, before, { timeout: 4000 });
        check((await nPanes(page)) === before + 1 && (await selected(page, 'service:svc-ig')) === 'true', `${key === ' ' ? 'Space' : key} on the focused pill starts exactly one sign-in`);
      }
      // A Ready pill only selects.
      const before = await nPanes(page);
      await page.click(plainSel('service:svc-ok'));
      check((await selected(page, 'service:svc-ok')) === 'true' && (await nPanes(page)) === before, 'a Ready pill: the click selects the tile and opens no pane');
      await page.click(plainSel('service:svc-nopage'));
      check((await selected(page, 'service:svc-nopage')) === 'true' && (await nPanes(page)) === before, 'a Needs sign-in pill with nothing to start: the click only selects');
      check(realErrors(pageErrors).length === 0, 'service pill: no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
      await ctx.close();
    }

    // Accounts: the pill starts the same route as the card head word (Browser first); no start-held, no wizard.
    for (const [id, url, profile] of [['acct-li', 'https://www.linkedin.com/login', 'linked_ronlevy'], ['acct-x', 'https://x.com/i/flow/login', 'x_ron']]) {
      const srv = makeServer();
      const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
      await page.waitForFunction((s) => (document.querySelector(s) || {}).textContent?.trim() === 'Needs sign-in', sel(id), { timeout: 6000 });
      const p = await pill(page, id);
      check(isActionPill(p) && overlaps(p), `${id}: the Needs sign-in pill is an action button over the tile's pill`, JSON.stringify(p));
      srv.log.length = 0;
      await page.click(sel(id));
      await page.waitForFunction(() => window.__panes.length === 1, null, { timeout: 4000 });
      const call = (await panes(page))[0];
      check((await selected(page, id)) === 'true' && !!(await page.$(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`)), `${id}: one click selects the tile and opens its card`);
      check(call[0] === url && call[3] === profile && (await nPanes(page)) === 1, `${id}: exactly one openBrowserPane call, the Browser route's page in its own profile`, JSON.stringify(call));
      const head = await page.$eval(`[data-conn-account="${id}"] .desk-v1-conn-head [data-conn-status]`, (e) => e.getAttribute('data-ss-profile'));
      check(head === profile, `${id}: the pill started the route the card head word names`, String(head));
      check(only(srv, /start-held|commit/).length === 0 && !(await page.$('[data-cfw]')), `${id}: no API sign-in, no wizard`);
      check(realErrors(pageErrors).length === 0, `${id} pill: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
      await ctx.close();
    }

    // An account that is Ready: the pill selects only.
    {
      const srv = makeServer();
      const { ctx, page } = await newPage(browser, srv, width, height);
      await page.waitForFunction((s) => (document.querySelector(s) || {}).textContent?.trim() === 'Ready', plainSel('acct-bare'), { timeout: 6000 });
      const p = await pill(page, 'acct-bare');
      await page.click(plainSel('acct-bare'));
      check(!p.action && p.text === 'Ready' && (await selected(page, 'acct-bare')) === 'true' && (await nPanes(page)) === 0, 'a Ready account pill: plain, the click selects and opens no pane', JSON.stringify(p));
      await ctx.close();
    }
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1440, 950);
  await run(browser, 390, 844);
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nPASS — Edit settings and Sign in are one button set per account; Sign in starts the real sign-in for its own route.');
