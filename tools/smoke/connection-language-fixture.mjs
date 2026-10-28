// Hermetic routing and data shared by Connections grid, language and status smokes.
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';
import { execFileSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connections_language');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true });
const BASELINE = {};
const beforeArg=process.argv.find(a=>a==='--before' || a.startsWith('--before='));
if(beforeArg) {
  const ref=beforeArg.split('=')[1] || 'HEAD';
  for(const name of ['static/index.html',...execFileSync('git',['diff','--name-only','--diff-filter=M',ref,'--','static'],{cwd:REPO_ROOT,encoding:'utf8'}).trim().split('\n').filter(Boolean)]) {
    BASELINE['/'+name]=execFileSync('git',['show',`${ref}:${name}`],{cwd:REPO_ROOT,encoding:'utf8'});
  }
}

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
  { ...NPM, server_name: 'nokey-mcp', state: 'credential_missing', missing: ['harmless.token'], credentials: [{ vault: 'harmless.token', env: 'HARMLESS_TOKEN' }], message: 'A credential it needs is not in the Vault.' },
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

export function makeServer({ connections = CONNECTIONS(), services = [] } = {}) {
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

export async function newPage(browser, { srv, width, height, wizard = false }) {
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
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: srv.baseline ? BASELINE['/static/index.html'] : INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: srv.baseline && BASELINE[path] || STATIC[path][1] });
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
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines:srv.engines || [] });
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path.startsWith('/api/desk/connect/permissions/') && method === 'GET') return J({policy:srv.policy || {state:'explicit',read:true,post:false,scopes:[]}});
    if (path === '/api/desk/connect/browser-profile/check') return J({state:srv.browserResult || 'verified',verifiable:true});
    if (path === '/api/desk/connect/verify') return J({state:srv.browserResult || 'verified'});
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
