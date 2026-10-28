#!/usr/bin/env node
/**
 * Desk v1 (Ron 2026-10-28) — a browser connection whose reading is not allowed yet is "Reading is off", never "Needs sign-in".
 *
 * Live repro: LinkedIn "Clayrune" (profile linked_ronlevy) was signed in, the pane showed his feed, and the card still said
 * "Needs sign-in" with "profile 'linked_ronlevy' is not switched on for agent reads". The profile was not on the agent-read
 * list, and the Edit settings > Permissions screen had no row to put linkedin.com on it. The page is the real one (real type
 * projection from mc.desk_connect, real Connections card, real Permissions step, real passcode prompt); the server and the
 * browser pane are stubs, so no real account or profile is touched.
 *
 *   1. Reads off      the grid card and the detail row say "Reading is off" (not Needs sign-in), in plain words, with ONE
 *                     primary button, Open permissions. It opens Edit settings at the Permissions step; opening writes nothing.
 *                     There: ONE list (Read this account's activity, Publish), no site row; Publish is disabled with a sentence.
 *                     Ticking Read and saving is the passcode-gated PUT /api/browser/profiles/<name>/agent-read with this
 *                     site added to the list, others kept. Cancelling the passcode sends nothing. The card then reads ok.
 *   2. Signed in, on  coverage ok: the card is ok ("Reading by browser"); no Open permissions, no Needs sign-in.
 *   3. Signed out     coverage not_connected without a gate: still "Needs sign-in", Sign in primary, no Open permissions.
 *   4. Reopened       Edit settings > Permissions for a LinkedIn account that reads through the browser profile shows the
 *                     same two rows (no site row), whether or not it also holds an API sign-in; unticking removes only
 *                     its own domain.
 *
 * RUN   cd tools/smoke && node desk-v1-reads-off.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'reads_off');
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
for plat in ('x', 'linkedin'):
    got = resolve.resolve('https://' + plat + '.com', own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    out['https://' + plat + '.com'] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const PASSCODE = 'right-passcode';
const PROFILE = 'linked_ronlevy';
const REASON = `profile '${PROFILE}' is not switched on for agent reads; only a human can switch it on`;
const GATE = { kind: 'reads_off', profile: PROFILE, site: 'linkedin.com' };
const COVERAGE = {
  reads_off: { platform: 'linkedin', via: 'pane', state: 'not_connected', reason: REASON, gate: GATE, message: 'Reading is off for linkedin.com' },
  ok: { platform: 'linkedin', via: 'pane', state: 'ok', reason: null, last_ok_at: '2026-10-28T09:00:00Z', message: '' },
  signed_out: { platform: 'linkedin', via: 'pane', state: 'not_connected', reason: 'The last read hit the sign-in page.', gate: null, message: 'Not connected (the last read hit the sign-in page)' },
};
const li = (extra) => ({ id: 'acct-li', platform: 'linkedin', identity: 'Clayrune', label: 'Clayrune', capability: 'direct', connected: true, voice: '',
  read_via: 'pane', browser_profile: PROFILE,
  browser_setup: { account_kind: 'organization', route_id: 'linkedin-browser', service: 'linkedin', refs: { browser_profile: PROFILE, login: 'ronlevy' } },
  publish: { ready: false, reason: 'not signed in', secret: null, unattended_ok: null }, ...extra });

function makeServer(account, coverage) {
  const fx = loadFixtures();
  const srv = { log: [], fx, accounts: [account], coverage, policy: { main: { enabled: true, domains: ['x.com'] }, [PROFILE]: { enabled: true, domains: ['x.com'] } } };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  return srv;
}

async function newPage(browser, srv, width, height) {
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
    if (path === '/api/browser/profiles' && method === 'GET') return J({ profiles: [{ name: 'main' }, { name: PROFILE }] });
    if (path === '/api/browser/agent-read' && method === 'GET') return J({ profiles: srv.policy });
    const put = path.match(/^\/api\/browser\/profiles\/([^/]+)\/agent-read$/);
    if (put && method === 'PUT') {
      let body = null; try { body = req.postDataJSON(); } catch (_) { /* none */ }
      srv.log.push({ method, path, body });
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.policy[put[1]] = { enabled: body.enabled, domains: body.domains };
      if (put[1] === PROFILE && body.domains.includes('linkedin.com')) srv.coverage = [COVERAGE.ok];     // reading is allowed: the next coverage read is ok
      return J({ ok: true, profile: put[1], enabled: body.enabled, domains: body.domains });
    }
    if (path.startsWith('/api/browser/')) { srv.log.push({ method, path, body: null }); return J({ error: 'the smoke has no browser' }, 500); }
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) return J({ project_id: 'p', coverage: srv.coverage });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/types' && method === 'POST') return J(TYPES[body && body.input] || { host: '', picker: [] });
    if (path === '/api/desk/connect/purposes') return J({ service: 'linkedin', accounts: [] });
    if (path === '/api/desk/connect/suggest') return J({ suggestions: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected' }, x: { state: 'not_connected' }, gemini: { saved: false }, openai: { saved: false } });
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({ policy: { state: 'legacy', scopes: [] } });
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
const selectTile = async (page, id) => {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 6000 });
};
const boxes = (page) => page.$$eval('[data-cfp-box]', (e) => e.map((x) => x.dataset.cfpBox));
const puts = (srv) => srv.log.filter((r) => r.method === 'PUT' && /agent-read$/.test(r.path));
const waitPrompt = (page) => page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
const answer = async (page, code) => { await waitPrompt(page); await page.fill('input[id^="hp-passcode-"]', code); await page.click('.modal-content .btn-add'); };
const cancel = async (page) => { await waitPrompt(page); await page.click('.modal-content .btn-secondary'); };
const tileState = (page) => page.$eval('[data-conn-tile="acct-li"]', (e) => ({ state: e.dataset.connState, text: e.textContent.replace(/\s+/g, ' ').trim() }));
const rowWord = (page) => text(page, '[data-conn-account="acct-li"] [data-cs-facet="browser"] [data-cs-word]');
const rowState = (page) => page.$eval('[data-conn-account="acct-li"] [data-cs-facet="browser"]', (e) => e.dataset.state);
const cardText = (page) => page.$eval('[data-conn-account="acct-li"]', (e) => e.innerText);
const waitCoverage = (page, word) => page.waitForFunction((w) => { const e = document.querySelector('[data-conn-account="acct-li"] [data-cs-facet="browser"] [data-cs-word]'); return e && e.textContent.trim() === w; }, word, { timeout: 6000 });

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1: reading is off ──────────────────────────────────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.reads_off]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await page.waitForFunction(() => /Reading is off/.test(document.querySelector('[data-conn-tile="acct-li"]').textContent), null, { timeout: 6000 });
    const tile = await tileState(page);
    check(/Reading is off/.test(tile.text) && !/Needs sign-in/.test(tile.text), `the grid card says "Reading is off", not "Needs sign-in" (${tile.text})`, 'grid card: ' + tile.text);
    await selectTile(page, 'acct-li');
    await waitCoverage(page, 'Reading is off');
    check((await rowWord(page)) === 'Reading is off' && (await rowState(page)) === 'readsoff', 'the detail status line says "Reading is off"', `row: ${await rowWord(page)} / ${await rowState(page)}`);
    const body = await cardText(page);
    check(!/Needs sign-in/.test(body) && !/agent_read_off|switched on for agent reads|domain_not_allowed/.test(body), 'no "Needs sign-in" and no internal term anywhere on the card', 'card text: ' + body);
    check(/Agents cannot read this account on linkedin\.com yet\. Switch it on in Permissions; it asks for your passcode\./.test(await text(page, '[data-conn-account="acct-li"] [data-cs-detail]')), 'one plain sentence says what to do', 'detail: ' + await text(page, '[data-conn-account="acct-li"] [data-cs-detail]'));
    const open = await page.$$eval('[data-conn-account="acct-li"] [data-ro-open]', (e) => e.map((x) => ({ text: x.textContent.trim(), primary: x.classList.contains('desk-v1-cf-primary') })));
    check(open.length === 1 && open[0].text === 'Open permissions' && open[0].primary, 'ONE action, Open permissions, and it is the primary button', JSON.stringify(open));
    const si = await page.$$eval('[data-conn-account="acct-li"] [data-as-signin]', (e) => e.map((x) => x.classList.contains('desk-v1-cf-primary')));
    check(si.length === 1 && si[0] === false, 'Sign in stays, but no longer primary', JSON.stringify(si));
    await page.screenshot({ path: resolve(SHOT_DIR, `reads_off_${width}.png`) });

    srv.log.length = 0;
    await page.click('[data-conn-account="acct-li"] [data-ro-open]');
    await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 6000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 6000 });
    check(puts(srv).length === 0, 'opening Permissions sent no write', 'a PUT went out: ' + JSON.stringify(puts(srv)));
    check((await boxes(page)).join('|') === 'read|post' && !(await page.$('[data-cfw-option="site"]')), 'Permissions is the one list, Read and Publish, with no site row', 'boxes: ' + (await boxes(page)).join('|'));
    const readOpt = await text(page, '[data-cfw-option="read"]'), postOpt = await text(page, '[data-cfw-option="post"]');
    check(/Read this account's activity/.test(readOpt) && !(await page.locator('[data-cfp-box="read"]').isDisabled()) && !(await page.locator('[data-cfp-box="read"]').isChecked()), 'Read this account\'s activity is enabled and off', readOpt);
    check(/Publish/.test(postOpt) && (await page.locator('[data-cfp-box="post"]').isDisabled()) && /Posting is not available through this connection yet\./.test(postOpt), 'Publish stays visible, disabled, with one sentence saying why', postOpt);
    check(!/linked_ronlevy|agent_read_off|profile/i.test(await text(page, '[data-cfp-rows]')), 'no profile name or internal term in the rows', await text(page, '[data-cfp-rows]'));
    await page.locator('[data-cfp-rows]').scrollIntoViewIfNeeded(); await page.screenshot({ path: resolve(SHOT_DIR, `permissions_from_reads_off_${width}.png`) });

    await page.check('[data-cfp-box="read"]');
    check(puts(srv).length === 0, 'ticking Read changes the draft only', 'a PUT went out on ticking');
    let done = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-li'));
    await waitPrompt(page);
    const prompt = await page.$eval('.modal-content:has(input[id^="hp-passcode-"])', (e) => e.innerText);
    check(/linkedin\.com/.test(prompt) && new RegExp(PROFILE).test(prompt), 'the passcode prompt names the site and the sign-in', prompt);
    await cancel(page);
    let r = await done;
    check(puts(srv).length === 0 && r.site.status === 'cancelled', 'cancelling the passcode prompt sends nothing', JSON.stringify([r, puts(srv)]));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await answer(page, PASSCODE);
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && sent[0].path === `/api/browser/profiles/${PROFILE}/agent-read` && sent[0].body.passcode === PASSCODE && sent[0].body.enabled === true
        && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com', 'linkedin.com']),
      'with the passcode: ONE PUT on this profile, linkedin.com added and x.com kept', JSON.stringify(sent.map((x) => [x.path, x.body])));
    check(srv.log.every((x) => x.method === 'GET' || x.method === 'PUT' || ['/api/desk/connect/types', '/api/desk/connect/purposes'].includes(x.path)), 'nothing else was written', JSON.stringify(srv.log.map((x) => x.method + ' ' + x.path)));
    await page.evaluate(() => window.DeskV1ConnectionStatus.invalidate());
    await page.evaluate(() => window.DeskV1ConnectWizard.close());
    await page.click('[data-conn-tile="acct-li"]');
    await waitCoverage(page, 'Reading by browser');
    const after = await tileState(page);
    check(after.state === 'ok' && !/Reading is off|Needs sign-in/.test(after.text), `the card then reads ok (${after.text})`, 'after: ' + after.text);
    check(!(await page.$('[data-conn-account="acct-li"] [data-ro-open]')), 'and Open permissions is gone', 'the button stayed');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 2: signed in, reading on ───────────────────────────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.ok]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await waitCoverage(page, 'Reading by browser');
    const tile = await tileState(page);
    check(tile.state === 'ok' && !/Reading is off|Needs sign-in/.test(tile.text), `signed in and allowed: the card is ok (${tile.text})`, 'tile: ' + tile.text);
    check(!(await page.$('[data-conn-account="acct-li"] [data-ro-open]')) && !/Needs sign-in|Reading is off/.test(await cardText(page)), 'no Open permissions and no sign-in warning', 'card: ' + await cardText(page));
    await page.screenshot({ path: resolve(SHOT_DIR, `connected_${width}.png`) });
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 3: truly signed out ────────────────────────────────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.signed_out]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await waitCoverage(page, 'Needs sign-in');
    const tile = await tileState(page);
    check(/Needs sign-in/.test(tile.text) && tile.state === 'reauth', `signed out: the card still says Needs sign-in (${tile.text})`, 'tile: ' + tile.text);
    const si = await page.$$eval('[data-conn-account="acct-li"] [data-as-signin]', (e) => e.map((x) => x.classList.contains('desk-v1-cf-primary')));
    check(si.length === 1 && si[0] === true && !(await page.$('[data-conn-account="acct-li"] [data-ro-open]')), 'Sign in is primary and there is no Open permissions', JSON.stringify(si));
    await page.screenshot({ path: resolve(SHOT_DIR, `signed_out_${width}.png`) });
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 4: the reopened account (Edit settings > Permissions) ──────────────────
  for (const [label, extra] of [['API-route account (also reads by browser)', { credentials: { oauth_vault: 'li.oauth' } }], ['browser-route account', {}]]) {
    const srv = makeServer(li(extra), [COVERAGE.reads_off]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await page.click('[data-conn-account="acct-li"] [data-cs-reopen]');
    await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 6000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 6000 });
    check((await boxes(page)).join('|') === 'read|post' && !(await page.$('[data-cfw-option="site"]')), `${label}: Permissions shows Read and Publish and no site row`, `${label}: boxes ${(await boxes(page)).join('|')}`);
    const readOpt = await text(page, '[data-cfw-option="read"]');
    check(/Read this account's activity/.test(readOpt) && !(await page.locator('[data-cfp-box="read"]').isDisabled()), `${label}: Read this account's activity is enabled`, readOpt);
    check(!(await page.locator('[data-cfp-box="read"]').isChecked()), `${label}: it is off while reading is off`, 'the box was ticked');
    check((await page.locator('[data-cfp-box="post"]').isDisabled()) && /Posting is not available through this connection yet\./.test(await text(page, '[data-cfw-option="post"]')), `${label}: Publish is visible, disabled, with the reason`, await text(page, '[data-cfw-option="post"]'));
    await page.locator('[data-cfp-rows]').scrollIntoViewIfNeeded(); await page.screenshot({ path: resolve(SHOT_DIR, `permissions_${label.startsWith('API') ? 'api' : 'browser'}_${width}.png`) });
    srv.log.length = 0;
    await page.check('[data-cfp-box="read"]');
    check(puts(srv).length === 0, `${label}: ticking changes the draft only`, 'a PUT went out on ticking');
    const pending = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(pending.permissionPending === true && pending.lines.some((l) => /linkedin\.com/.test(l)), `${label}: the Review line says what will be allowed`, JSON.stringify(pending));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await answer(page, PASSCODE);
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && sent[0].path === `/api/browser/profiles/${PROFILE}/agent-read` && sent[0].body.passcode === PASSCODE
        && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com', 'linkedin.com']),
      `${label}: Save is the passcode-gated PUT on that profile, linkedin.com added, x.com kept`, JSON.stringify(sent.map((x) => [x.path, x.body])));
    check(srv.log.filter((x) => x.method === 'POST' && x.path.includes('/permissions/commit')).length === 0, `${label}: no Desk scope was written (this route has none to record)`, 'a Desk commit went out');
    check(realErrors(pageErrors).length === 0, `${label}: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 5: already allowed -> Read is on; unticking removes only its own domain ─────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.ok]);
    srv.policy[PROFILE] = { enabled: true, domains: ['x.com', 'linkedin.com'] };
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await page.click('[data-conn-account="acct-li"] [data-cs-reopen]');
    await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 6000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 6000 });
    check(await page.locator('[data-cfp-box="read"]').isChecked(), 'allowed account: Read this account\'s activity shows on', 'the box was off');
    srv.log.length = 0;
    await page.uncheck('[data-cfp-box="read"]');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await answer(page, PASSCODE);
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com']), 'unticking removes linkedin.com and keeps x.com', JSON.stringify(sent.map((x) => x.body)));
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
console.log('\nPASS — reading-off is its own state with one human-only action; signed-out still says Needs sign-in; Permissions is one list for every account, with no site row.');
