#!/usr/bin/env node
/**
 * Desk v1 — a saved sign-in behind a LOCKED vault is not "sign in again" (Ron, 2026-10-06: "The Higgsfield
 * connection established yesterday had to be re-connected again today"; the idle relock hid a good, refreshable
 * sign-in and the card said Needs sign-in). Against a fake server, `desk_v1_live` ON, at 1440 and 390 wide.
 * The engines route answers `connected.state: 'vault_locked'` while the vault is locked, and a connected
 * engine after the real unlock call shape (`POST /api/secrets/vault-lock/unlock`). Asserts:
 *   - the Higgsfield tile is on the grid with the pill "Vault locked", not "Needs sign-in";
 *   - its card offers "Unlock the vault" and NO sign-in button, and says the sign-in is still saved;
 *   - the inline unlock (desk-v1-vault-gate.js) is already showing, with no sign-in clicked first;
 *   - unlocking sends exactly the passphrase and the passcode, the card re-reads the engines and becomes
 *     Connected, and no sign-in flow was started (no POST .../higgsfield/start, no vendor page opened);
 *   - nothing overflows sideways.
 *
 * RUN   cd tools/smoke && node desk-v1-vault-locked-card.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const PASSCODE = 'right-passcode';
const PASSPHRASE = 'right-vault-passphrase';
const LOCKED_REASON = 'Your vault is locked. Unlock it; your sign-in is still saved.';

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, locked: true };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  srv.engines = () => [
    { id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', auth: { kind: 'oauth', vault_entry: 'oauth.higgsfield', service: 'higgsfield' }, currency: 'credits', group: 'higgsfield', advanced: false,
      job_limit_usd: null, job_limit_credits: null,
      connected: srv.locked
        ? { ready: false, vault_entry: 'oauth.higgsfield', exists: true, state: 'vault_locked', reason: LOCKED_REASON }
        : { ready: true, vault_entry: 'oauth.higgsfield', exists: true, state: 'connected', reason: null },
      models: [{ model_id: 'soul_2', kind: 'image', label: 'Soul 2', status: 'stable', aspect_ratios: ['1:1'] }] },
  ];
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskGuidePollMs = 40; window.__panes = []; });
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
    })));
    if (path === '/api/telemetry/summary') return J({});
    if (path === '/api/features') return J({});
    if (path === '/api/config/models') return J({ models: [] });
    if (path === '/api/config' && method === 'GET') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/secrets/vault-lock' && method === 'GET') {
      srv.log.push({ method, path, body });
      return J({ state: srv.locked ? 'locked' : 'unlocked', configured: true, legacy_key_copies_present: false });
    }
    if (path === '/api/secrets/vault-lock/unlock' && method === 'POST') {
      srv.log.push({ method, path, body });
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (body.passphrase !== PASSPHRASE) return J({ error: 'wrong_passphrase', message: 'That passphrase is not right.' }, 403);
      srv.locked = false;
      return J({ ok: true, state: 'unlocked' });
    }
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: srv.engines() });
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: srv.locked ? 'vault_locked' : 'connected', reason: srv.locked ? LOCKED_REASON : null }, x: { state: 'not_connected', app: {} }, gemini: { saved: false }, openai: { saved: false } });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => {
    window.humanProofFetch = async (url, init) => {
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
    window.openBrowserPane = async (url, pid, sid, profile) => { window.__panes.push({ url, profile }); };
  });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const gates = (page) => page.$$eval('[data-vault-gate]', (g) => g.filter((e) => !e.hidden).length);

async function lockedCard(browser, width, height) {
  console.log(`live ON: Higgsfield signed in, vault locked, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });

  await page.waitForSelector('[data-conn-tile="engine:higgsfield_mcp"]', { timeout: 6000 }).catch(() => {});
  const tile = await page.$('[data-conn-tile="engine:higgsfield_mcp"]');
  check(!!tile, 'the Higgsfield tile is on the grid', 'no Higgsfield tile');
  const pill = tile ? (await tile.textContent()) : '';
  check(/Vault locked/.test(pill) && !/Needs sign-in/.test(pill), 'its pill says "Vault locked", not "Needs sign-in"', `tile text: ${pill.trim()}`);
  await tile.click();
  await page.waitForSelector('[data-conn-engine="higgsfield_mcp"]', { timeout: 6000 });
  check((await page.textContent('[data-engine-status]')).trim() === 'Vault locked', 'the card status is "Vault locked"', 'the card status is not "Vault locked"');
  check(/sign-in is still saved/.test(await page.textContent('[data-engine-locked-note]')), 'the card says the sign-in is still saved', 'the saved-sign-in note is missing');
  check(!!(await page.$('[data-engine-unlock]')), 'the card offers "Unlock the vault"', 'no unlock action on the card');
  check(!(await page.$('[data-engine-signin]')), 'the card offers no sign-in button', 'the card still offers a sign-in');
  await page.waitForSelector('[data-vault-gate]:not([hidden])', { timeout: 4000 }).catch(() => {});
  check((await gates(page)) === 1, 'the inline unlock is already showing', 'no inline unlock on the locked card');
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(over <= 0, 'no horizontal scroll', `horizontal overflow ${over}px`);

  await page.fill('[data-vg-pass]', PASSPHRASE);
  await page.fill('[data-vg-passcode]', PASSCODE);
  await page.click('[data-vg-unlock]');
  await page.waitForFunction(() => /Connected/.test(document.querySelector('[data-engine-status]')?.textContent || ''), null, { timeout: 5000 }).catch(() => {});
  const posts = srv.log.filter((r) => r.method === 'POST' && r.path === '/api/secrets/vault-lock/unlock');
  check(posts.length === 1 && Object.keys(posts[0].body).sort().join() === 'passcode,passphrase', 'the unlock sent the passphrase and the passcode and nothing else', `unlock posts: ${JSON.stringify(posts.map((p) => Object.keys(p.body)))}`);
  check(/Connected/.test(await page.textContent('[data-engine-status]')), 'after the unlock the card is Connected', 'the card did not return to Connected');
  check(!(await page.$('[data-engine-unlock]')), 'the unlock action is gone', 'the unlock action is still there');
  check(!srv.log.some((r) => /\/higgsfield\/start$/.test(r.path)), 'no sign-in flow was started', 'a sign-in flow was started');
  check((await page.evaluate(() => window.__panes.length)) === 0, 'the vendor sign-in page was never opened', 'the vendor page was opened');
  check(realErrors(pageErrors).length === 0, 'no page errors', `page errors: ${realErrors(pageErrors).join(' | ')}`);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) await lockedCard(browser, w, h);
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nAll checks passed');
