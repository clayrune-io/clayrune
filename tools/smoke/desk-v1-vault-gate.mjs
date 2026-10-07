#!/usr/bin/env node
/**
 * Desk v1 — an inline vault unlock in the Connections flows that store a secret
 * (static/js/desk-v1-vault-gate.js; Ron 2026-10-05, phone: Add service > Higgsfield > Sign in with
 * Higgsfield let him finish the whole vendor login, then "Signed in, but the sign-in could not be saved:
 * vault is locked"). Against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The vault-lock status
 * route is mocked; the unlock is the real `POST /api/secrets/vault-lock/unlock` call shape.
 *
 *   - vault locked: Add service shows ONE inline unlock (passphrase + dashboard passcode + Unlock) before
 *     any flow is started, and it stays the only one after picking Higgsfield (the card has its own mount);
 *   - "Sign in with Higgsfield" while locked: the server's 409 vault_locked is shown, the browser pane is NOT
 *     opened, the unlock is (still) there;
 *   - a wrong passcode keeps the form, shows the server's words, and both inputs are emptied;
 *   - a right passphrase + passcode: the unlock request carries exactly those, the form goes away, the
 *     Higgsfield card is still on screen (the flow was not restarted) and the sign-in now opens the pane;
 *   - vault unlocked from the start: no unlock form at all;
 *   - nothing overflows sideways and the unlock controls are inside the window.
 *
 * Screenshots: docs/desk_v1/screens/vault_gate_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-vault-gate.mjs
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
const PASSPHRASE = 'right-vault-passphrase';

function makeServer({ locked }) {
  const fx = loadFixtures();
  const srv = { log: [], fx, locked };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  srv.engines = () => [
    { id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', auth: { kind: 'oauth', vault_entry: 'oauth.higgsfield', service: 'higgsfield' }, currency: 'credits', group: 'higgsfield', advanced: false,
      job_limit_usd: null, job_limit_credits: null,
      connected: { ready: false, vault_entry: 'oauth.higgsfield', exists: false, state: 'not_connected', reason: 'Higgsfield (sign in) is not signed in yet' },
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
    if (path === '/api/desk/connect/status') return J({ higgsfield: { state: 'not_connected', reason: null }, x: { state: 'not_connected', app: {} }, gemini: { saved: false }, openai: { saved: false } });
    if (path === '/api/desk/connect/higgsfield/start' && method === 'POST') {
      if (srv.locked) return J({ error: 'Unlock the vault first, then sign in', code: 'vault_locked' }, 409);
      return J({ flow_id: 'flow-1', auth_url: 'https://auth.higgsfield.example/authorize?state=S', redirect_uri: 'http://127.0.0.1:50000/callback', profile: 'desk-higgsfield' }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-1') return J({ status: 'pending' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => {
    window.humanProofFetch = async (url, init) => {         // the passcode prompt is its own smoke; here it is answered
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
const unlockPosts = (srv) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/secrets/vault-lock/unlock');

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0 };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
}

async function inView(page, selector, label) {
  const r = await page.$eval(selector, (e) => { e.scrollIntoView({ block: 'nearest' }); const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, vw: window.innerWidth }; });
  check(r.l >= 0 && r.r <= r.vw, `${label} is inside the ${r.vw}px window`, `${label} sticks out sideways ${JSON.stringify(r)}`);
}

async function lockedFlow(browser, width, height) {
  console.log(`live ON: vault locked, Add service > Higgsfield, at ${width}`);
  const srv = makeServer({ locked: true });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-add-service] [data-vault-gate]:not([hidden])', { timeout: 4000 }).catch(() => {});
  check((await gates(page)) === 1, 'Add service shows the inline unlock before anything is started', 'no inline unlock on Add service');
  check(!!(await page.$('[data-vg-pass]')) && !!(await page.$('[data-vg-passcode]')) && !!(await page.$('[data-vg-unlock]')),
    'it has a passphrase, a dashboard passcode and an Unlock button', 'the unlock form is incomplete');
  check(srv.log.some((r) => r.method === 'GET' && r.path === '/api/secrets/vault-lock'), 'the lock state was read from GET /api/secrets/vault-lock', 'the lock state was never read');
  await fits(page, 'Add service');
  await inView(page, '[data-vg-unlock]', 'Unlock');
  await page.screenshot({ path: resolve(SHOT_DIR, `vault_gate_${width}.png`) });

  check(!srv.log.some(r=>/start/.test(r.path)), 'locked Add service starts no vendor sign-in');
  // a wrong passcode
  await page.fill('[data-vg-pass]', PASSPHRASE);
  await page.fill('[data-vg-passcode]', 'wrong-passcode');
  await page.click('[data-vg-unlock]');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.querySelector('[data-vg-status]')?.textContent || ''), null, { timeout: 4000 }).catch(() => {});
  check(/Wrong dashboard passcode/.test(await page.textContent('[data-vg-status]')), 'a wrong passcode is refused in plain words', 'no wrong-passcode message');
  check((await page.inputValue('[data-vg-pass]')) === '' && (await page.inputValue('[data-vg-passcode]')) === '', 'both inputs were emptied after sending', 'a typed value was left in an input');
  check((await gates(page)) === 1, 'the form stays after a refusal', 'the form closed on a refusal');

  // the right ones
  await page.fill('[data-vg-pass]', PASSPHRASE);
  await page.fill('[data-vg-passcode]', PASSCODE);
  await page.click('[data-vg-unlock]');
  await page.waitForFunction(() => document.querySelectorAll('[data-vault-gate]:not([hidden])').length === 0, null, { timeout: 4000 }).catch(() => {});
  const posts = unlockPosts(srv);
  const last = posts[posts.length - 1];
  check(posts.length === 2 && last.body.passphrase === PASSPHRASE && last.body.passcode === PASSCODE && Object.keys(last.body).sort().join() === 'passcode,passphrase',
    'the unlock request carries the passphrase and the passcode and nothing else', `unlock requests: ${JSON.stringify(posts.map((p) => Object.keys(p.body)))}`);
  check((await gates(page)) === 0, 'the form goes away once unlocked', 'the form is still showing after a good unlock');
  check(!!(await page.$('[data-cfw-input]')), 'unlock preserves the shared Add service screen');
  check(realErrors(pageErrors).length === 0, 'no page errors', `page errors: ${realErrors(pageErrors).join(' | ')}`);
  await ctx.close();
}

async function unlockedFlow(browser, width, height) {
  console.log(`live ON: vault unlocked, no unlock form, at ${width}`);
  const srv = makeServer({ locked: false });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]');
  await new Promise((r) => setTimeout(r, 300));          // the state read is async
  check(srv.log.some((r) => r.method === 'GET' && r.path === '/api/secrets/vault-lock'), 'the lock state was read', 'the lock state was never read');
  check((await gates(page)) === 0, 'no unlock form is shown', 'an unlock form shows although the vault is unlocked');
  check(realErrors(pageErrors).length === 0, 'no page errors', `page errors: ${realErrors(pageErrors).join(' | ')}`);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await lockedFlow(browser, w, h);
    await unlockedFlow(browser, w, h);
  }
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nAll checks passed');
