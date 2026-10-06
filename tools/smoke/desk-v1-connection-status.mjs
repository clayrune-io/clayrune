#!/usr/bin/env node
/**
 * Desk v1 — Connections management status (MC-1062 ticket 13b, docs/desk_v1/connect_flow_tickets/13b-management-status.md),
 * against a fake server, `desk_v1_live` ON. static/js/desk-v1-connection-status.js; the names-only `browser_setup` on an
 * account row is pinned server-side by tests/test_desk_accounts.py.
 *
 *   accounts  a browser connection that reads is not "Not connected" because publishing is closed; Publishing stays its own
 *             line; an X account with a browser profile AND an API sign-in keeps both rows, each with its own profile
 *             name and route; Needs sign-in, Browser profile saved and Read only each say their own word; a site with
 *             no route stays Not connected; a refresh of the page opens no browser and makes no probe.
 *   editor    with the wizard off, the Read via controls stay; with it on they give way to ONE Change button.
 *   mcp       every approved npm and remote server is a tile with its own word: Registered, Saved cannot run yet,
 *             Saved setup failed, Changed since approved, Credential missing, Package missing, Server changed; a saved
 *             reference stays "Saved for agents"; drift paths, the observed remote diff, and the recovery actions are
 *             shown; Refresh status reads saved state only; Check the connection runs only when pressed; Accept asks
 *             for the passcode and says nothing is Verified.
 *   + phone   no horizontal scroll at 390.
 *
 * Screenshots: docs/desk_v1/screens/connection_status_{accounts,mcp_npm,mcp_remote}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connection-status.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
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
const OBSERVED = 'sha256:' + 'a'.repeat(64);

const NPM = { ecosystem: 'npm', scope: 'project', project_id: 'p1', package: 'harmless-mcp', version: '1.2.3', credentials: [], approved_at: '2026-10-01T10:00:00Z' };
const DRIFT = { status: 'changed', reason: 'files_differ', counts: { changed: 1, added: 2, removed: 1 }, changed: ['package/dist/index.js'], added: ['package/dist/planted0.js', 'package/dist/planted1.js'], removed: ['package/package.json'], more: 3, checked: 40 };
const REMOTE = { ecosystem: 'remote', scope: 'global', project_id: null, package: 'https://mcp.example.com/mcp', protocol: 'streamable_http', credentials: [{ vault: 'example.token', header: 'Authorization' }], approved_at: '2026-10-02T10:00:00Z' };
const CONNECTIONS = () => [
  { ...NPM, server_name: 'steady-mcp', state: 'registered', message: 'Registered. It starts the first time an agent session uses it.', package_files: { status: 'unchanged', counts: { changed: 0, added: 0, removed: 0 }, changed: [], added: [], removed: [], more: 0 } },
  { ...NPM, server_name: 'drifted-mcp', state: 'changed', code: 'package_files_changed', package_files: DRIFT, message: 'The package files on disk no longer match what was approved.' },
  { ...NPM, server_name: 'pending-mcp', state: 'pending_runtime', message: 'Saved. This install has no secret wrapper, so nothing is registered yet.' },
  { ...NPM, server_name: 'failed-mcp', state: 'setup_failed', message: 'Saved, but registering it failed.' },
  { ...NPM, server_name: 'nokey-mcp', state: 'credential_missing', missing: ['harmless.token'], credentials: [{ vault: 'harmless.token', env: 'HARMLESS_TOKEN' }], message: 'A credential it needs is not in Secrets.' },
  { ...NPM, server_name: 'gone-mcp', state: 'package_missing', message: 'The package is not on disk.' },
  { ...REMOTE, server_name: 'steady-remote', state: 'registered', message: 'Registered.', observation: { status: 'unchanged', review_needed: false, checked_at: '2026-10-05T09:00:00Z', tool_count: 2, diff: [] } },
  { ...REMOTE, server_name: 'moved-remote', state: 'registered', message: 'Registered.', observation: { status: 'changed', review_needed: true, observed: OBSERVED, checked_at: '2026-10-05T09:00:00Z', tool_count: 3, diff: [{ what: 'new tool', detail: 'delete_everything' }] } },
  { ...REMOTE, server_name: 'fresh-remote', state: 'registered', message: 'Registered.', observation: { status: 'never_checked', review_needed: false } },
];

// Which accounts the page is given, and what each read route has said (the free coverage read).
function accountsFor(fx) {
  const base = (id, over) => {
    const c = JSON.parse(JSON.stringify(fx.channels.find((x) => x.id === id)));
    return { ...c, publish: { ready: false, reason: 'not signed in', secret: null, unattended_ok: null }, credentials: null, preview: false, ...over };
  };
  return [
    // X, browser profile reads fine, API not signed in: NOT "Not connected".
    base('ch-x-ron', { browser_setup: { service: 'x', route_id: 'x-browser', account_kind: 'member', refs: { browser_profile: 'ron-x', login: 'x.ron' }, saved_at: '2026-10-01T00:00:00Z' }, browser_profile: 'ron-x' }),
    // X, browser AND API: both identities.
    base('ch-x-clayrune', { publish: { ready: true, reason: null, secret: null, unattended_ok: true }, credentials: { oauth_profile: 'clayrune-oauth' },
      browser_setup: { service: 'x', route_id: 'x-browser', account_kind: 'brand', refs: { browser_profile: 'clayrune-x', login: 'x.clayrune' }, saved_at: '2026-10-01T00:00:00Z' }, browser_profile: 'clayrune-x' }),
    // LinkedIn: a saved profile whose last read hit the sign-in page.
    base('ch-li-page', { browser_profile: 'li-page' }),
    // YouTube: read only.
    base('ch-yt-clayrune', { capability: 'none' }),
    // Reddit: a saved profile, nothing read through it yet, no coverage row.
    base('ch-reddit', { browser_setup: { service: 'reddit', route_id: 'reddit-browser', account_kind: 'member', refs: { browser_profile: 'rd' }, saved_at: '2026-10-01T00:00:00Z' } }),
    // Discord: no route at all.
    base('ch-discord-community', {}),
  ];
}

function makeServer({ connections = CONNECTIONS(), services = [] } = {}) {
  const fx = loadFixtures();
  const srv = { log: [], fx, connections, services, checkOutcome: 'unchanged' };
  srv.accounts = accountsFor(fx);
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  srv.coverage = () => [
    { platform: 'x', via: 'pane', state: 'ok', last_ok_at: '2026-10-05T08:30:00Z', message: '' },
    { platform: 'linkedin', via: 'pane', state: 'not_connected', reason: 'The last read hit the sign-in page.', message: 'Not connected (the last read hit the sign-in page)' },
  ];
  return srv;
}

async function newPage(browser, { srv, width, height, wizard = false }) {
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
    if (!path.startsWith('/api/desk/')) { srv.log.push({ method, path, body: null, other: true }); return route.abort(); }
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path.startsWith('/api/desk/engagement/coverage/')) return J({ coverage: srv.coverage() });
    if (path === '/api/desk/connect/custom/connections' && method === 'POST') return J({ connections: srv.connections });
    if (path === '/api/desk/connect/custom/remote/check' && method === 'POST') {
      const changed = srv.checkOutcome === 'changed';
      return J({ ok: true, server_name: body.server_name, status: srv.checkOutcome, diff: changed ? [{ what: 'new tool', detail: 'delete_everything' }] : [], review_needed: changed,
        checked_at: '2026-10-06T00:00:00Z', observed: OBSERVED, tool_count: 3, truncated: false, purpose_verified: false,
        meaning: 'The server answered, and the tools it offers are recorded. This does not show that it does what you want it for.' });
    }
    if (path === '/api/desk/connect/custom/remote/adopt' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.connections = srv.connections.map((r) => (r.server_name === body.server_name ? { ...r, observation: { status: 'adopted', review_needed: false, observed: body.observed, checked_at: '2026-10-06T00:00:00Z', tool_count: 3, diff: [] } } : r));
      return J({ ok: true, server_name: body.server_name, status: 'adopted', diff: [], review_needed: false, observed: body.observed, tool_count: 3, truncated: false, purpose_verified: false });
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  if (wizard) await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  // The coverage read and the saved approvals arrive after the first paint; wait for both to land.
  await page.waitForFunction(() => document.querySelector('[data-conn-tile][data-conn-kind="mcp"]') && document.querySelector('[data-conn-tile="ch-x-ron"] [data-conn-tile-status]').textContent !== 'Not connected', null, { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connection_status_${name}_${w}.png`) });
const word = (page, key) => page.$eval(`[data-conn-tile="${key}"] [data-conn-tile-status]`, (e) => e.textContent.trim());
// Anything that is not a plain read: a browser launch or input, a verify, a write, or an MCP check nobody pressed.
const unwanted = (srv) => srv.log.filter((r) => /connect\/verify$/.test(r.path) || /remote\/(check|adopt)$/.test(r.path) || (/\/api\/browser\//.test(r.path) && r.method !== 'GET')
  || (r.method !== 'GET' && !/connect\/custom\/connections$/.test(r.path)));

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement, scroller = document.querySelector('[data-connections]');
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0 };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
}

async function accountsScenario(browser, width, height) {
  console.log(`Accounts at ${width}px: a status that comes from the routes, not from publishing alone`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });

  const w = {};
  for (const id of ['ch-x-ron', 'ch-x-clayrune', 'ch-li-page', 'ch-yt-clayrune', 'ch-reddit', 'ch-discord-community']) w[id] = await word(page, id);
  check(w['ch-x-ron'] === 'Reading by browser', 'X with a profile that reads, API not signed in: "Reading by browser", not "Not connected"', 'ch-x-ron: ' + w['ch-x-ron']);
  check(w['ch-x-clayrune'] === 'Connected', 'X with the API signed in: Connected', 'ch-x-clayrune: ' + w['ch-x-clayrune']);
  check(w['ch-li-page'] === 'Needs sign-in', 'LinkedIn whose last read hit the sign-in page: "Needs sign-in"', 'ch-li-page: ' + w['ch-li-page']);
  check(w['ch-yt-clayrune'] === 'Read only', 'a read-only site: "Read only"', 'ch-yt: ' + w['ch-yt-clayrune']);
  check(w['ch-reddit'] === 'Browser profile saved', 'a saved profile nothing has read through: "Browser profile saved", never signed-in or Verified', 'ch-reddit: ' + w['ch-reddit']);
  check(w['ch-discord-community'] === 'Not connected', 'a site with no route: "Not connected"', 'ch-discord: ' + w['ch-discord-community']);
  check(new Set(Object.values(w)).size === 6, 'the six words are all different', 'words: ' + JSON.stringify(w));
  check(!Object.values(w).includes('Verified'), 'no account is called Verified by a saved profile or an answering route', 'a Verified word appeared');

  // X with browser only: two rows, publishing separate.
  await page.click('[data-conn-tile="ch-x-ron"]');
  await page.waitForSelector('[data-cs-account="ch-x-ron"]');
  const rows = await page.$$eval('[data-cs-account="ch-x-ron"] [data-cs-facet]', (e) => e.map((x) => [x.dataset.csFacet, x.dataset.state, x.querySelector('[data-cs-word]').textContent]));
  check(JSON.stringify(rows) === JSON.stringify([['browser', 'ok', 'Reading by browser'], ['api', 'off', 'API not signed in']]), 'the detail has a Browser row and an API row, each with its own word', 'rows: ' + JSON.stringify(rows));
  const pub = await page.$eval('[data-conn-account="ch-x-ron"] [data-conn-publish]', (e) => [e.dataset.ready, e.textContent.replace(/\s+/g, ' ').trim()]);
  check(pub[0] === 'false' && /Publishing/.test(pub[1]) && /not connected/.test(pub[1]), 'Publishing stays its own line and still says not connected', 'publishing line: ' + pub);
  const browserText = await page.textContent('[data-cs-facet="browser"]');
  check(/ron-x/.test(browserText) && /x\.ron/.test(browserText) && /x-browser/.test(browserText), 'the Browser row names its profile, its login and its route', 'browser row: ' + browserText);
  check(/Last read 2026-10-05 08:30/.test(browserText), 'it says when the last read worked', 'no last-read time: ' + browserText);
  check((await page.$$('[data-readvia-row]')).length === 1, 'with the wizard off the Read via controls stay (the only editor)', 'read via rows: ' + (await page.$$('[data-readvia-row]')).length);
  check((await page.$$('[data-cs-reopen]')).length === 0, 'and there is no second Change button next to them', 'a Change button beside Read via');
  await shot(page, 'accounts', width);
  await fits(page, 'accounts');

  // X with both routes keeps both identities.
  await page.click('[data-conn-tile="ch-x-clayrune"]');
  await page.waitForSelector('[data-cs-account="ch-x-clayrune"]');
  const both = await page.$$eval('[data-cs-account="ch-x-clayrune"] [data-cs-facet]', (e) => e.map((x) => [x.dataset.csFacet, x.querySelector('[data-cs-word]').textContent, x.textContent]));
  check(both.length === 2 && both[0][0] === 'browser' && both[1][0] === 'api', 'an account with browser and API shows both routes', 'both: ' + JSON.stringify(both.map((b) => b[0])));
  check(/clayrune-x/.test(both[0][2]) && /x\.clayrune/.test(both[0][2]) && /clayrune-oauth/.test(both[1][2]) && /x-api/.test(both[1][2]) && both[1][1] === 'API signed in',
    'each keeps its own profile, login and route: browser clayrune-x / x.clayrune, API clayrune-oauth / x-api', 'both rows: ' + JSON.stringify(both));

  // LinkedIn needs sign-in.
  await page.click('[data-conn-tile="ch-li-page"]');
  await page.waitForSelector('[data-cs-account="ch-li-page"]');
  const li = await page.textContent('[data-cs-account="ch-li-page"] [data-cs-facet="browser"]');
  check(/Needs sign-in/.test(li) && /sign-in page/.test(li) && /li-page/.test(li), 'LinkedIn says Needs sign-in, why, and which profile', 'linkedin row: ' + li);

  // No route.
  await page.click('[data-conn-tile="ch-discord-community"]');
  await page.waitForSelector('[data-cs-none]');
  ok('a site with no route says so in its detail');

  check(unwanted(srv).length === 0, 'the page opened no browser, ran no verify and wrote nothing', 'unwanted requests: ' + JSON.stringify(unwanted(srv).map((r) => r.method + ' ' + r.path)));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function editorScenario(browser, width, height) {
  console.log(`Editor at ${width}px: one place to change a connection once the wizard is on`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height, wizard: true });
  await page.click('[data-conn-tile="ch-x-ron"]');
  await page.waitForSelector('[data-cs-account="ch-x-ron"]');
  check((await page.$$('[data-readvia-row]')).length === 0 && (await page.$$('[data-readvia]')).length === 0, 'with the wizard on the duplicate Read via controls are gone', 'Read via still on screen');
  check((await page.$$('[data-cs-reopen]')).length === 1 && /Browser pane/.test(await page.textContent('[data-cs-readvia]')), 'one Change button and a one-line summary of how it is read', 'no Change button or summary');
  await page.click('[data-cs-reopen]');
  await page.waitForSelector('[data-conn-detail="add"]', { timeout: 4000 });
  ok('Change opens the Add service panel (the setup editor) until the wizard is wired to it');
  check(unwanted(srv).length === 0, 'Change wrote nothing', 'unwanted: ' + JSON.stringify(unwanted(srv).map((r) => r.method + ' ' + r.path)));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function mcpScenario(browser, width, height) {
  console.log(`MCP at ${width}px: every approved server shows its real state`);
  const srv = makeServer({ services: [{ id: 'svc1', name: 'Notes site', url: 'https://notes.example.com', created: '2026-10-01', credential: {} }] });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });

  const tiles = await page.$$eval('[data-conn-tile][data-conn-kind="mcp"]', (e) => e.map((x) => [x.title, x.querySelector('[data-conn-tile-status]').textContent.trim(), x.dataset.connState]));
  const by = Object.fromEntries(tiles.map((t) => [t[0], t[1]]));
  check(tiles.length === 9, 'every approved npm and remote server is a tile (9)', 'mcp tiles: ' + tiles.length);
  const expect = { 'steady-mcp': 'Registered', 'drifted-mcp': 'Changed since approved', 'pending-mcp': 'Saved, cannot run yet', 'failed-mcp': 'Saved, setup failed', 'nokey-mcp': 'Credential missing',
    'gone-mcp': 'Package missing', 'steady-remote': 'Registered', 'moved-remote': 'Server changed', 'fresh-remote': 'Registered' };
  check(Object.entries(expect).every(([k, v]) => by[k] === v), 'each tile has the word its record earned', 'words: ' + JSON.stringify(by));
  check(await page.$eval('[data-conn-tile="service:svc1"]', (e) => e.querySelector('[data-conn-tile-status]').textContent.trim() === 'Saved for agents'), 'a saved reference stays "Saved for agents", apart from every MCP word', 'saved service word changed');
  check(!Object.values(by).includes('Verified') && !Object.values(by).includes('Connected'), 'a registered server is never called Verified or Connected', 'by: ' + JSON.stringify(by));

  // Drift.
  await page.click('[data-conn-tile="mcp:project:p1:drifted-mcp"]');
  await page.waitForSelector('[data-conn-mcp]');
  const paths = await page.$$eval('[data-cs-drift-path]', (e) => e.map((x) => x.dataset.csDriftPath + ':' + x.querySelector('code').textContent));
  check(paths.length === 4 && paths[0] === 'changed:package/dist/index.js' && paths.includes('removed:package/package.json') && /and 3 more/.test(await page.textContent('[data-conn-mcp]')), 'drift lists changed, added and removed paths and how many more', 'paths: ' + JSON.stringify(paths));
  check((await page.$$('[data-cs-reopen]')).length === 1 && (await page.$$('[data-cs-check-run]')).length === 0, 'a changed npm server offers Review again and no check', 'actions wrong');
  const facts = await page.textContent('[data-cs-facts]');
  check(/harmless-mcp/.test(facts) && /1\.2\.3/.test(facts) && /Package that runs on this computer/.test(facts) && /Approved/.test(facts), 'its facts: package, version, kind, approved', 'facts: ' + facts);
  await shot(page, 'mcp_npm', width);
  await fits(page, 'npm detail');
  await page.click('[data-cs-reopen]');
  await page.waitForSelector('[data-conn-detail="add"]', { timeout: 4000 });
  ok('Review again opens the setup editor');

  await page.click('[data-conn-tile="mcp:project:p1:nokey-mcp"]');
  await page.waitForSelector('[data-cs-missing]');
  check(/harmless\.token/.test(await page.textContent('[data-cs-missing]')) && /HARMLESS_TOKEN/.test(await page.textContent('[data-cs-credential]')), 'Credential missing names the Secrets entry and where it goes, never a value', 'missing line wrong');
  await page.click('[data-conn-tile="mcp:project:p1:failed-mcp"]');
  await page.waitForSelector('[data-conn-mcp]');
  check((await page.$$('[data-cs-reopen]')).length === 1, 'setup failed offers Review again', 'no recovery on setup_failed');
  await page.click('[data-conn-tile="mcp:project:p1:pending-mcp"]');
  await page.waitForSelector('[data-conn-mcp]');
  check((await page.$$('[data-cs-reopen]')).length === 0, 'cannot-run-yet is a state of this install, not something Review again fixes: no button', 'a button on pending_runtime');

  // Remote.
  const before = srv.log.length;
  await page.click('[data-conn-tile="mcp:global::moved-remote"]');
  await page.waitForSelector('[data-cs-observed="changed"]');
  check(/delete_everything/.test(await page.textContent('[data-cs-diff]')) && /not been told to trust/.test(await page.textContent('[data-cs-observed]')), 'an observed remote change stays visible with its diff, untrusted until accepted', 'observed block wrong');
  check(/Authorization/.test(await page.textContent('[data-cs-credential]')) && /example\.token/.test(await page.textContent('[data-cs-credential]')), 'its credential shows as a Secrets entry and header, no value', 'credential line wrong');
  await shot(page, 'mcp_remote', width);
  await fits(page, 'remote detail');
  check(srv.log.slice(before).every((r) => r.method === 'GET' || r.path.endsWith('connect/custom/connections')), 'opening the detail made no network call to the server', 'calls: ' + JSON.stringify(srv.log.slice(before)));

  // Refresh: one local read, no check.
  const n0 = srv.log.filter((r) => /custom\/connections$/.test(r.path)).length;
  await page.click('[data-cs-refresh]');
  await page.waitForTimeout(300);
  const n1 = srv.log.filter((r) => /custom\/connections$/.test(r.path)).length;
  check(n1 === n0 + 1 && srv.log.filter((r) => /remote\/check$/.test(r.path)).length === 0, 'Refresh status re-reads the saved list once and checks nothing', `refresh made ${n1 - n0} list read(s), checks ${srv.log.filter((r) => /remote\/check$/.test(r.path)).length}`);
  check(/starts nothing and contacts no server/.test(await page.textContent('[data-cs-refresh-note]')), 'and says so', 'no refresh note');

  // Accept: passcode, then the server is no longer flagged.
  await page.click('[data-cs-adopt]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  check(srv.log.filter((r) => /remote\/adopt$/.test(r.path)).length === 0, 'nothing is accepted until the passcode is given', 'adopt ran before the passcode');
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
  await page.waitForSelector('[data-cs-observed="adopted"]', { timeout: 4000 });
  const adopt = srv.log.filter((r) => /remote\/adopt$/.test(r.path))[0].body;
  check(adopt.observed === OBSERVED && adopt.server_name === 'moved-remote' && adopt.scope === 'global', 'Accept sends the server, its scope and the observation that was shown', 'adopt body: ' + JSON.stringify(adopt));
  check(await word(page, 'mcp:global::moved-remote') === 'Registered', 'after accepting, the tile reads Registered again', 'tile after adopt: ' + await word(page, 'mcp:global::moved-remote'));

  // Check the connection runs only when pressed.
  await page.click('[data-conn-tile="mcp:global::fresh-remote"]');
  await page.waitForSelector('[data-cs-check-run]');
  check(srv.log.filter((r) => /remote\/check$/.test(r.path)).length === 0, 'no check has run: nobody pressed it', 'a check ran by itself');
  await page.click('[data-cs-check-run]');
  await page.waitForSelector('[data-cs-observed="unchanged"]', { timeout: 4000 });
  const chk = srv.log.filter((r) => /remote\/check$/.test(r.path));
  check(chk.length === 1 && chk[0].body.server_name === 'fresh-remote' && /does not show that it does what you want/.test(await page.textContent('[data-conn-mcp]')), 'Check the connection ran once, on that server, and says an answer is not proof it does what you want', 'check log: ' + JSON.stringify(chk));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await accountsScenario(browser, w, h);
    await editorScenario(browser, w, h);
    await mcpScenario(browser, w, h);
  }
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
