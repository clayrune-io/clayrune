#!/usr/bin/env node
// Reading-off and signed-out stay distinct. Existing browser grants always stay
// visible and revocable, even without a projected route. LinkedIn can grant new
// reads; Instagram cannot. Hermetic production UI at 1440 and 390 widths.

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
for plat in ('x', 'linkedin', 'instagram'):
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
  const srv = { log: [], fx, accounts: [account], coverage, types: structuredClone(TYPES), permission: { state: 'legacy', scopes: [] }, policy: { main: { enabled: true, domains: ['x.com'] }, [PROFILE]: { enabled: true, domains: ['x.com'] } } };
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
    if (path === '/api/desk/connect/types' && method === 'POST') return J(srv.types[body && body.input] || { host: '', picker: [] });
    if (path === '/api/desk/connect/purposes') return J({ service: 'linkedin', accounts: [] });
    if (path === '/api/desk/connect/suggest') return J({ suggestions: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected' }, x: { state: 'not_connected' }, gemini: { saved: false }, openai: { saved: false } });
    if (path === '/api/desk/connect/permissions/commit') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.permission = { state: 'explicit', scopes: body.draft.scopes };
      return J({ policy: srv.permission });
    }
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({ policy: srv.permission });
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

  if (!process.argv.includes('--revocation-only')) {
  // ── 1: reading is off ──────────────────────────────────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.reads_off]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await page.waitForFunction(() => /Not checked yet/.test(document.querySelector('[data-conn-tile="acct-li"]').textContent), null, { timeout: 6000 });
    const tile = await tileState(page);
    check(/Not checked yet/.test(tile.text) && !/Needs sign-in/.test(tile.text), `the grid card says "Reading is off", not "Needs sign-in" (${tile.text})`, 'grid card: ' + tile.text);
    await selectTile(page, 'acct-li');
    await waitCoverage(page, 'Not checked yet');
    check((await rowWord(page)) === 'Not checked yet' && (await rowState(page)) === 'readsoff', 'the detail status line says "Reading is off"', `row: ${await rowWord(page)} / ${await rowState(page)}`);
    const body = await cardText(page);
    check(!/Needs sign-in/.test(body) && !/agent_read_off|switched on for agent reads|domain_not_allowed/.test(body), 'no "Needs sign-in" and no internal term anywhere on the card', 'card text: ' + body);
    check(/Agents cannot read this account on linkedin\.com yet\. Switch it on in Permissions; it asks for your passcode\./.test(await text(page, '[data-conn-account="acct-li"] [data-cs-detail]')), 'one plain sentence says what to do', 'detail: ' + await text(page, '[data-conn-account="acct-li"] [data-cs-detail]'));
    const open = await page.$$eval('[data-conn-account="acct-li"] [data-cs-reopen]', (e) => e.map((x) => ({ text: x.textContent.trim(), primary: x.classList.contains('desk-v1-cf-primary') })));
    check(open.length === 1 && open[0].text === 'Edit', 'ONE action, Open permissions, and it is the primary button', JSON.stringify(open));
    const si = await page.$$eval('[data-conn-account="acct-li"] [data-as-signin]', (e) => e.map((x) => x.classList.contains('desk-v1-cf-primary')));
    check(si.length === 0, 'Sign in stays, but no longer primary', JSON.stringify(si));
    await page.screenshot({ path: resolve(SHOT_DIR, `reads_off_${width}.png`) });

    srv.log.length = 0;
    await page.click('[data-conn-account="acct-li"] [data-cs-reopen]');
    await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 6000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 6000 });
    check(puts(srv).length === 0, 'opening Permissions sent no write', 'a PUT went out: ' + JSON.stringify(puts(srv)));
    check((await boxes(page)).join('|') === 'read|post' && !(await page.$('[data-cfw-option="site"]')), 'Permissions is the one list, Read and Publish, with no site row', 'boxes: ' + (await boxes(page)).join('|'));
    const readOpt = await text(page, '[data-cfw-option="read"]'), postOpt = await text(page, '[data-cfw-option="post"]');
    check(!await page.locator('[data-cfp-box="read"]').isDisabled() && !await page.locator('[data-cfp-box="read"]').isChecked(), 'LinkedIn Read is enabled and off without a grant', readOpt);
    check(/Publish/.test(postOpt) && (await page.locator('[data-cfp-box="post"]').isDisabled()) && /Posting is not available through this connection yet\./.test(postOpt), 'Publish stays visible, disabled, with one sentence saying why', postOpt);
    check(!/linked_ronlevy|agent_read_off|profile/i.test(await text(page, '[data-cfp-rows]')), 'no profile name or internal term in the rows', await text(page, '[data-cfp-rows]'));
    await page.locator('[data-cfp-rows]').scrollIntoViewIfNeeded(); await page.screenshot({ path: resolve(SHOT_DIR, `permissions_from_reads_off_${width}.png`) });

    await page.check('[data-cfp-box="read"]');
    check(puts(srv).length === 0, 'ticking Read changes only the draft', JSON.stringify(puts(srv)));
    const cancelled = page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-li'));
    await cancel(page);
    const r = await cancelled;
    check(r.desk.status === 'cancelled' && r.site.status === 'not_attempted' && puts(srv).length === 0, 'cancelling Desk consent sends no browser grant', JSON.stringify(r));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await answer(page, PASSCODE);       // Desk consent
    await answer(page, PASSCODE);       // browser domain, a separate human gate
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && sent[0].body.passcode === PASSCODE && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com', 'linkedin.com']), 'passcode Save adds linkedin.com and keeps x.com', JSON.stringify(sent));
    check(srv.permission.scopes.length > 0 && srv.permission.scopes.every(s => s.route_id === 'linkedin-browser' && s.purpose === 'read_own'), 'Save records only LinkedIn browser read scopes', JSON.stringify(srv.permission));
    await page.evaluate(() => window.DeskV1ConnectWizard.close());
    await page.click('[data-conn-tile="acct-li"]');
    check((await page.$$('[data-cs-reopen]')).length===1,'one shared Edit remains','Edit missing or doubled');
    check(realErrors(pageErrors).length === 0, 'no uncaught page errors', JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  // ── 2: signed in, reading on ───────────────────────────────────────────────
  {
    const srv = makeServer(li(), [COVERAGE.ok]);
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await waitCoverage(page, 'Ready');
    const tile = await tileState(page);
    check(tile.state === 'ok' && !/Reading is off|Needs sign-in/.test(tile.text), `signed in and allowed: the card is ok (${tile.text})`, 'tile: ' + tile.text);
    check((await page.$$('[data-conn-account="acct-li"] [data-cs-reopen]')).length===1 && !/Needs sign-in|Reading is off/.test(await cardText(page)), 'no Open permissions and no sign-in warning', 'card: ' + await cardText(page));
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
    check(si.length === 1 && si[0] === true && (await page.$$('[data-conn-account="acct-li"] [data-cs-reopen]')).length===1, 'Sign in is primary and there is no Open permissions', JSON.stringify(si));
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
    check(!await page.locator('[data-cfp-box="read"]').isDisabled(), `${label}: LinkedIn browser Read is offered`, readOpt);
    check(!(await page.locator('[data-cfp-box="read"]').isChecked()), `${label}: it is off while reading is off`, 'the box was ticked');
    check((await page.locator('[data-cfp-box="post"]').isDisabled()) && /Posting is not available through this connection yet\./.test(await text(page, '[data-cfw-option="post"]')), `${label}: Publish is visible, disabled, with the reason`, await text(page, '[data-cfw-option="post"]'));
    await page.locator('[data-cfp-rows]').scrollIntoViewIfNeeded(); await page.screenshot({ path: resolve(SHOT_DIR, `permissions_${label.startsWith('API') ? 'api' : 'browser'}_${width}.png`) });
    srv.log.length = 0;
    await page.check('[data-cfp-box="read"]');
    const pending = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(pending.permissionPending && pending.lines.some(l => /linkedin\.com/.test(l)), `${label}: Review names the browser grant`, JSON.stringify(pending));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await answer(page, PASSCODE);
    await answer(page, PASSCODE);
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com', 'linkedin.com']), `${label}: Save adds only linkedin.com`, JSON.stringify(sent));
    check(srv.permission.scopes.length > 0 && srv.permission.scopes.every(s => s.route_id === 'linkedin-browser'), `${label}: existing reader scopes are recorded`, JSON.stringify(srv.permission));
    check(realErrors(pageErrors).length === 0, `${label}: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
    await ctx.close();
  }

  }

  // 5: prior LinkedIn grant is on; revocation works even with an old/missing runtime projection.
  // 6: stale Instagram grant is also shown and revocable, although no new Read is offered.
  for (const [service, missingRuntime] of [['linkedin', false], ['linkedin', true], ['instagram', true]]) {
    const account = service === 'linkedin' ? li() : li({platform: 'instagram', identity: '@fixture', label: 'Instagram fixture', browser_setup: {service: 'instagram', account_kind: 'account', route_id: 'instagram-browser', refs: {browser_profile: PROFILE}}});
    const srv = makeServer(account, [{...COVERAGE.ok, platform: service}]);
    const domain = service + '.com', label = `${service}${missingRuntime ? ' without projected runtime' : ''}`;
    if (missingRuntime) srv.types['https://' + domain].picker.forEach(t => t.variants.forEach(v => {v.runtime.purposes = {};}));
    srv.policy[PROFILE] = { enabled: true, domains: ['x.com', domain] };
    const { ctx, page, pageErrors } = await newPage(browser, srv, width, height);
    await selectTile(page, 'acct-li');
    await page.click('[data-conn-account="acct-li"] [data-cs-reopen]');
    await page.waitForSelector('[data-conn-detail="add"] [data-cfw-step="permissions"]', { timeout: 6000 });
    await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'), null, { timeout: 6000 });
    const box = page.locator('[data-cfp-box="read"]');
    check(!await box.isDisabled() && await box.isChecked() && !await box.evaluate(e => e.indeterminate), `${label}: existing domain grant is checked and enabled`, 'the live grant was hidden');
    const before = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.summary());
    check(!before.permissionPending && before.lines.some(l => l.includes(domain)), `${label}: unchanged Review discloses the live domain grant`, JSON.stringify(before));
    await page.locator('[data-cfp-rows]').scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(SHOT_DIR, `revoke_${service}_${missingRuntime ? 'missing' : 'declared'}_${width}.png`)});
    srv.log.length = 0;
    // Report each pre-fix failure without waiting on a known-disabled control.
    if (await box.isDisabled()) { await ctx.close(); continue; }
    await box.uncheck();
    check(puts(srv).length === 0, `${label}: unticking drafts revocation only`, JSON.stringify(puts(srv)));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    if (!missingRuntime) await answer(page, PASSCODE);   // explicit Desk deny for a legacy account
    await answer(page, PASSCODE);
    await page.waitForFunction(() => document.querySelector('[data-cfw-step="result"]'), null, { timeout: 6000 });
    const sent = puts(srv);
    check(sent.length === 1 && sent[0].body.passcode === PASSCODE && JSON.stringify(sent[0].body.domains) === JSON.stringify(['x.com']), `${label}: unticking removes only ${domain}`, JSON.stringify(sent));
    check(missingRuntime ? srv.log.every(x => !x.path.includes('/permissions/commit')) : srv.permission.scopes.length === 0, `${label}: revocation grants no Desk scope`, JSON.stringify(srv.permission));
    if (service === 'instagram') {
      await page.evaluate(() => window.DeskV1ConnectWizard.close());
      await selectTile(page, 'acct-li');
      await page.click('[data-conn-account="acct-li"] [data-cs-reopen]');
      await page.waitForSelector('[data-cfw-step="permissions"]');
      await page.waitForFunction(() => !document.querySelector('[data-cfp-loading]'));
      const off = page.locator('[data-cfp-box="read"]');
      check(await off.isDisabled() && !await off.isChecked() && /Reading isn't available for Instagram yet/.test(await text(page, '[data-cfw-option="read"]')), 'Instagram without a grant stays unavailable after revocation', await text(page, '[data-cfw-option="read"]'));
      srv.log.length = 0;
      await off.evaluate(e => {e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));});
      const result = await page.evaluate(() => window.DeskV1ConnectPermissionsStep.apply('acct-li'));
      check(!await off.isChecked() && result.site.status === 'unchanged' && puts(srv).length === 0, 'synthetic disabled Instagram change cannot restore a grant', JSON.stringify(result));
    }
    check(realErrors(pageErrors).length === 0, `${label}: no uncaught page errors`, JSON.stringify(realErrors(pageErrors)));
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
