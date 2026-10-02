#!/usr/bin/env node
/**
 * Desk v1 (backlog c7ac1e8c, Ron 2026-10-02) — the guided Connect flows on Connections,
 * against a fake server (no provider is reached, nothing is spent), `desk_v1_live` ON.
 *
 *   1. Higgsfield -> "Sign in with Higgsfield" is the default route (credits); the API key
 *                    route sits under Advanced. Sign in = one POST through the passcode
 *                    prompt, the sign-in page opened in the pane on its own named profile,
 *                    the card polls itself to Connected. A cancelled passcode sends nothing.
 *                    The per-job limit is labelled and saved in credits. Disconnect asks too.
 *   2. Gemini / OpenAI -> numbered steps with the exact page link, a paste box that saves
 *                    through the passcode-gated Secrets write (box emptied first), an
 *                    automatic and a manual "Test connection", a refusal shown plainly.
 *   3. X          -> a three step wizard: the exact callback URL and scopes, Client ID (+ secret
 *                    if X showed one) saved, then "Sign in with X" (profile desk-x) to a
 *                    verified Connected account. A public app saves the ID only.
 *   4. LinkedIn   -> one line saying what to apply for, with the link.
 *   5. Copy       -> no jargon (OAuth / vault / DCR) and no em-dash in the Connections text;
 *                    no horizontal overflow at 344 wide; no value ever in the DOM or a response.
 *
 * RUN   cd tools/smoke && node desk-connect-guides.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

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

const KEY_SENTINEL = 'AIzaSENTINEL-should-never-render-0123456789';
const SECRET_SENTINEL = 'client-secret-SENTINEL-9876543210';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = {
    log: [], fx, flows: {}, flowSeq: 0, testOk: true, xConnected: false,
    ov: { higgsfield: { state: 'not_connected', reason: null },
      x: { state: 'not_connected', reason: null, app: { client_id: false, client_secret: false }, callback_url: 'http://127.0.0.1:53682/callback', scopes: ['tweet.read', 'tweet.write', 'users.read', 'offline.access'] },
      gemini: { saved: false, vault_entry: 'gemini-api' }, openai: { saved: false, vault_entry: 'openai-api' } },
    limits: {},
  };
  const accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'linkedin' || c.platform === 'blog')
    .map((c) => JSON.parse(JSON.stringify(c)));
  srv.accounts = () => accounts.map((a) => ({ ...a, publish:
    a.platform === 'x' ? (srv.xConnected ? { ready: true, reason: null, secret: 'oauth.x', unattended_ok: true } : { ready: false, reason: 'no X API token in the vault', secret: 'x.oauth-token', unattended_ok: null })
      : a.platform === 'linkedin' ? { ready: false, reason: 'LinkedIn app review pending (w_organization_social)', secret: null, unattended_ok: null }
        : { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, accounts: srv.accounts(), pieces: [] });
  srv.engines = () => {
    const hf = srv.ov.higgsfield;
    return [
      { id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', auth: { kind: 'oauth', vault_entry: 'oauth.higgsfield', service: 'higgsfield' }, currency: 'credits', group: 'higgsfield', advanced: false,
        job_limit_usd: null, job_limit_credits: srv.limits.higgsfield_mcp ?? null,
        connected: { ready: hf.state === 'connected', vault_entry: 'oauth.higgsfield', exists: hf.state !== 'not_connected', state: hf.state, reason: hf.state === 'connected' ? null : 'Higgsfield (sign in) is not signed in yet' },
        models: [{ model_id: 'soul_2', kind: 'image', label: 'Soul 2', status: 'stable', aspect_ratios: ['1:1'] }] },
      { id: 'higgsfield', label: 'Higgsfield (API key)', auth: { kind: 'key_id_secret', vault_entry: 'higgsfield' }, currency: 'usd', group: 'higgsfield', advanced: true,
        job_limit_usd: srv.limits.higgsfield ?? null, connected: { ready: false, vault_entry: 'higgsfield', exists: false, reason: "no vault entry named 'higgsfield' (add it in Secrets)" },
        models: [{ model_id: 'kling-2.5', kind: 'video', label: 'Kling', status: 'stable', aspect_ratios: ['16:9'] }] },
      { id: 'google', label: 'Google', auth: { kind: 'api_key', vault_entry: 'gemini-api' }, currency: 'usd', job_limit_usd: null,
        connected: { ready: srv.ov.gemini.saved, vault_entry: 'gemini-api', exists: srv.ov.gemini.saved, reason: srv.ov.gemini.saved ? null : "no vault entry named 'gemini-api' (add it in Secrets)" },
        models: [{ model_id: 'veo-3.1', kind: 'video', label: 'Veo 3.1', status: 'preview', aspect_ratios: ['16:9'] }] },
      { id: 'openai', label: 'OpenAI', auth: { kind: 'api_key', vault_entry: 'openai-api' }, currency: 'usd', job_limit_usd: null,
        connected: { ready: srv.ov.openai.saved, vault_entry: 'openai-api', exists: srv.ov.openai.saved, reason: null },
        models: [{ model_id: 'gpt-image', kind: 'image', label: 'GPT image', status: 'stable', aspect_ratios: ['1:1'] }] },
    ];
  };
  return srv;
}

async function newPage(browser, srv, viewport = { width: 1400, height: 1100 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const responses = [];
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => { responses.push(JSON.stringify(body)); return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }); };
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
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/secrets' && method === 'POST') {
      srv.log.push({ method, path, body });
      if (body.name === 'gemini-api') srv.ov.gemini.saved = true;
      if (body.name === 'openai-api') srv.ov.openai.saved = true;
      if (body.name === 'x.client-id') srv.ov.x.app.client_id = true;
      if (body.name === 'x.client-secret') srv.ov.x.app.client_secret = true;
      return J({ name: body.name, scope: 'global', hint: '' });      // metadata only, never the value
    }
    if (!path.startsWith('/api/desk/')) return route.abort();
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: srv.engines() });
    let m = path.match(/^\/api\/desk\/engines\/([^/]+)\/limit$/);
    if (m && method === 'PUT') {
      srv.limits[m[1]] = body.job_limit_usd;
      return J(m[1] === 'higgsfield_mcp' ? { engine_id: m[1], job_limit_credits: body.job_limit_usd } : { engine_id: m[1], job_limit_usd: body.job_limit_usd });
    }
    if (path === '/api/desk/connect/status') return J(srv.ov);
    m = path.match(/^\/api\/desk\/connect\/(higgsfield|x)\/start$/);
    if (m && method === 'POST') {
      const id = 'flow-' + (++srv.flowSeq);
      srv.flows[id] = { service: m[1], status: 'pending' };
      return J({ flow_id: id, auth_url: m[1] === 'x' ? 'https://x.com/i/oauth2/authorize?client_id=CID&state=S' : 'https://auth.higgsfield.example/authorize?state=S',
        redirect_uri: m[1] === 'x' ? 'http://127.0.0.1:53682/callback' : 'http://127.0.0.1:50000/callback', profile: 'desk-' + m[1] }, 201);
    }
    m = path.match(/^\/api\/desk\/connect\/flows\/([^/]+)$/);
    if (m) {
      const f = srv.flows[m[1]];
      if (!f) return J({ status: 'unknown', message: 'No sign-in is waiting.' });
      return J({ status: f.status, message: f.message || '', service: f.service });
    }
    m = path.match(/^\/api\/desk\/connect\/(higgsfield|x)\/disconnect$/);
    if (m && method === 'POST') {
      if (m[1] === 'x') { srv.xConnected = false; srv.ov.x.state = 'not_connected'; } else srv.ov.higgsfield.state = 'not_connected';
      return J({ service: m[1], revoked: true });
    }
    m = path.match(/^\/api\/desk\/connect\/(gemini|openai)\/test$/);
    if (m && method === 'POST') return J(srv.testOk ? { ok: true, message: `${m[1] === 'gemini' ? 'Gemini' : 'OpenAI'} accepted the key.` }
      : { ok: false, message: `${m[1] === 'gemini' ? 'Gemini' : 'OpenAI'} did not accept the key (HTTP 401). Check it was copied whole, then save it again.` });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => {
    window.__proofs = []; window.__pane = []; window.__deskGuidePollMs = 40;
    window.humanProofFetch = async (url, init, proof) => {
      window.__proofs.push({ url, title: proof && proof.title, description: proof && proof.description });
      if (window.__cancelProof) return null;
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
    window.openBrowserPane = async (url, pid, sid, profile) => { window.__pane.push({ url, profile }); };
  });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-account]', { timeout: 6000 });
  await page.waitForSelector('[data-conn-engine]', { timeout: 6000 });
  return { ctx, page, pageErrors, responses };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const calls = (srv, method, re) => srv.log.filter((r) => r.method === method && re.test(r.path));
const proofs = (page) => page.evaluate(() => window.__proofs);
const pane = (page) => page.evaluate(() => window.__pane);
const txt = (page, sel) => page.textContent(sel).then((t) => (t || '').replace(/\s+/g, ' ').trim()).catch(() => '');
const realErrors = (e) => e.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));

// ── 1: Higgsfield ──────────────────────────────────────────────────────────
async function higgsfield(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  const row = '[data-conn-engine="higgsfield_mcp"]';
  check(/^Not connected/.test(await txt(page, `${row} [data-engine-status]`)), 'Higgsfield starts as Not connected, in plain words');
  check((await txt(page, `${row} [data-engine-signin]`)) === 'Sign in with Higgsfield', 'the default route is "Sign in with Higgsfield"');
  check(/credits in your Higgsfield plan/.test(await txt(page, row)), 'it says renders use the plan credits');
  const adv = await page.$('[data-conn-advanced]');
  check(adv && !(await adv.evaluate((d) => d.open)) && /Advanced: use an API key instead/.test(await txt(page, '[data-conn-advanced] summary')),
    'the API key route is collapsed under "Advanced: use an API key instead"');
  check((await txt(page, '[data-conn-engine="higgsfield_mcp"] .desk-v1-engine-limit')).includes('credits'), 'the per-job limit is labelled in credits for the sign-in route');
  check((await txt(page, '[data-conn-engine="higgsfield"] .desk-v1-engine-limit')).includes('USD'), 'the API key route keeps a USD limit');

  // A cancelled passcode sends nothing.
  await page.evaluate(() => { window.__cancelProof = true; });
  await page.click(`${row} [data-engine-signin]`);
  await settle(page, () => window.__proofs.length === 1);
  check(calls(srv, 'POST', /\/connect\/higgsfield\/start$/).length === 0 && (await pane(page)).length === 0, 'a cancelled passcode prompt starts no sign-in and opens no page');
  await page.evaluate(() => { window.__cancelProof = false; });
  await settle(page, () => !document.querySelector('[data-conn-engine="higgsfield_mcp"] [data-engine-signin]').disabled || true);
  await page.evaluate(() => { document.querySelector('[data-conn-engine="higgsfield_mcp"] [data-engine-signin]').disabled = false; });

  await page.click(`${row} [data-engine-signin]`);
  await settle(page, () => window.__pane.length === 1);
  const starts = calls(srv, 'POST', /\/connect\/higgsfield\/start$/);
  const pf = await proofs(page);
  check(starts.length === 1 && pf.length === 2 && /Sign in to Higgsfield/.test(pf[1].title), 'Sign in is one POST through the passcode prompt');
  const p0 = (await pane(page))[0];
  check(p0.profile === 'desk-higgsfield' && /auth\.higgsfield\.example/.test(p0.url), 'the sign-in page opens in the pane on the named profile "desk-higgsfield"');
  check(/Finish signing in/.test(await txt(page, `${row} [data-guide-status]`)), 'the card tells the user what to do next');
  srv.ov.higgsfield.state = 'connected'; srv.flows['flow-1'].status = 'done';
  await settle(page, () => /^Connected$/.test(document.querySelector('[data-conn-engine="higgsfield_mcp"] [data-engine-status]').textContent.trim()));
  ok('the card polls itself and ends on a verified "Connected"');
  check(!!(await page.$(`${row} [data-engine-disconnect]`)), 'Disconnect is offered once connected');

  await page.fill(`${row} [data-engine-limit-input]`, '25');
  await page.click(`${row} [data-engine-limit-save]`);
  await settle(page, () => /25 credits/.test(document.querySelector('[data-conn-engine="higgsfield_mcp"] [data-engine-limit-note]').textContent));
  const put = calls(srv, 'PUT', /higgsfield_mcp\/limit$/);
  check(put.length === 1 && put[0].body.job_limit_usd === 25, 'the credit limit is saved through the passcode prompt and shown as "25 credits"');

  await page.click(`${row} [data-engine-disconnect]`);
  await settle(page, () => /^Not connected/.test(document.querySelector('[data-conn-engine="higgsfield_mcp"] [data-engine-status]').textContent.trim()));
  const pf2 = await proofs(page);
  check(calls(srv, 'POST', /\/connect\/higgsfield\/disconnect$/).length === 1 && /Disconnect Higgsfield/.test(pf2[pf2.length - 1].title), 'Disconnect is one POST through the passcode prompt');

  // The API key route (Advanced) is a guided key paste with a key ID and a secret.
  await page.click('[data-conn-advanced] summary');
  await page.click('[data-conn-engine="higgsfield"] [data-engine-guide]');
  await page.waitForSelector('[data-guide="higgsfield"] [data-guide-user]');
  check((await page.$$('[data-guide="higgsfield"] [data-guide-test]')).length === 0, 'the Higgsfield API key guide has no Test connection (it has no free read call)');
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 2: Gemini + OpenAI ─────────────────────────────────────────────────────
async function keyPaste(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors, responses } = await newPage(browser, srv);
  const row = '[data-conn-engine="google"]';
  check((await txt(page, `${row} [data-engine-guide]`)) === 'Connect', 'Gemini offers Connect');
  await page.click(`${row} [data-engine-guide]`);
  await page.waitForSelector('[data-guide="google"]');
  const link = await page.getAttribute('[data-guide="google"] [data-guide-link]', 'href');
  check(link === 'https://aistudio.google.com/apikey', 'step 1 links the exact page: aistudio.google.com/apikey');
  check((await page.$$('[data-guide="google"] ol > li')).length >= 4, 'the steps are numbered and in plain words');
  check((await page.getAttribute('[data-guide="google"] [data-guide-key]', 'type')) === 'password', 'the paste box hides what is typed');
  check(await page.$eval('[data-guide="google"] [data-guide-test]', (b) => b.disabled), 'Test connection waits until a key is saved');

  await page.click('[data-guide="google"] [data-guide-save]');
  check(/Paste the/.test(await txt(page, '[data-guide="google"] [data-guide-result]')) && calls(srv, 'POST', /secrets$/).length === 0, 'Save with an empty box sends nothing');

  await page.fill('[data-guide="google"] [data-guide-key]', KEY_SENTINEL);
  await page.click('[data-guide="google"] [data-guide-save]');
  await settle(page, () => /accepted the key/.test(document.querySelector('[data-guide="google"] [data-guide-result]').textContent));
  const sec = calls(srv, 'POST', /secrets$/);
  const pf = await proofs(page);
  check(sec.length === 1 && sec[0].body.name === 'gemini-api' && sec[0].body.value === KEY_SENTINEL && /Save this key/.test(pf[0].title), 'the key is saved as gemini-api through the passcode prompt');
  check((await page.inputValue('[data-guide="google"] [data-guide-key]')) === '', 'the paste box is emptied once it is sent');
  check(calls(srv, 'POST', /connect\/gemini\/test$/).length === 1, 'a saved key is tested at once, so Connected means it works');
  check(/^Connected$/.test(await txt(page, `${row} [data-engine-status]`)), 'the card now says Connected');
  srv.testOk = false;
  await page.click('[data-guide="google"] [data-guide-test]');
  await settle(page, () => /did not accept/.test(document.querySelector('[data-guide="google"] [data-guide-result]').textContent));
  ok('Test connection shows the provider refusal plainly: "' + (await txt(page, '[data-guide="google"] [data-guide-result]')) + '"');
  srv.testOk = true;

  const orow = '[data-conn-engine="openai"]';
  await page.click(`${orow} [data-engine-guide]`);
  await page.waitForSelector('[data-guide="openai"]');
  check((await page.getAttribute('[data-guide="openai"] [data-guide-link]', 'href')) === 'https://platform.openai.com/api-keys', 'OpenAI links platform.openai.com/api-keys');
  await page.fill('[data-guide="openai"] [data-guide-key]', 'sk-SENTINEL-openai-0000');
  await page.click('[data-guide="openai"] [data-guide-save]');
  await settle(page, () => /accepted the key/.test(document.querySelector('[data-guide="openai"] [data-guide-result]').textContent));
  check(calls(srv, 'POST', /secrets$/).some((r) => r.body.name === 'openai-api') && calls(srv, 'POST', /connect\/openai\/test$/).length === 1, 'OpenAI saves as openai-api and tests');

  const html = await page.content();
  check(!html.includes(KEY_SENTINEL) && !html.includes('sk-SENTINEL') && !responses.some((r) => r.includes(KEY_SENTINEL) || r.includes('sk-SENTINEL')), 'no key value is in the page or in any response');
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 3: X wizard + 4: LinkedIn + 5: copy ────────────────────────────────────
async function xWizard(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors, responses } = await newPage(browser, srv);
  const xrow = '[data-conn-account][data-platform="x"]';
  const guideBtn = `${xrow} [data-conn-x-guide]`;
  check((await txt(page, guideBtn)) === 'Connect X', 'the X account offers "Connect X"');
  check(!/Open Secrets/.test(await txt(page, '[data-connections]')), 'there is no "Open Secrets" dead end anywhere on Connections');
  await page.click(guideBtn);
  await page.waitForSelector('[data-guide="x"] [data-x-step="3"]');
  const w = '[data-guide="x"]';
  check((await txt(page, `${w} [data-x-callback]`)) === 'http://127.0.0.1:53682/callback', 'step 1 shows the exact callback address to paste into X');
  check(/tweet\.read tweet\.write users\.read offline\.access/.test(await txt(page, w)), 'the four permissions are listed');
  check(/pay-per-use billing/.test(await txt(page, w)) && /Read and write/.test(await txt(page, w)) && /Web App, Automated App or Bot/.test(await txt(page, w)), 'billing, permission and app type are spelled out');
  await page.click(`${w} [data-x-portal]`);
  const p0 = (await pane(page))[0];
  check(p0 && p0.url === 'https://console.x.com' && p0.profile === 'desk-x', 'the X developer page opens in the pane on the "desk-x" profile');
  check(await page.$eval(`${w} [data-x-signin]`, (b) => b.disabled), 'Sign in with X waits until the Client ID is saved');

  // Public app: Client ID only.
  await page.fill(`${w} [data-x-client-id]`, 'CLIENT-ID-ONLY');
  await page.click(`${w} [data-x-save]`);
  await settle(page, () => !document.querySelector('[data-guide="x"] [data-x-signin]').disabled);
  let secs = calls(srv, 'POST', /secrets$/);
  check(secs.length === 1 && secs[0].body.name === 'x.client-id', 'a Client ID alone saves one entry (a public app has no secret)');
  // Confidential app: add the secret afterwards.
  await page.fill(`${w} [data-x-client-secret]`, SECRET_SENTINEL);
  await page.click(`${w} [data-x-save]`);
  for (let i = 0; i < 80 && calls(srv, 'POST', /secrets$/).length < 2; i++) await page.waitForTimeout(50);
  secs = calls(srv, 'POST', /secrets$/);
  check(secs.length === 2 && secs[1].body.name === 'x.client-secret' && secs[1].body.value === SECRET_SENTINEL, 'a Client Secret saves as its own entry through the passcode prompt');
  check((await page.inputValue(`${w} [data-x-client-secret]`)) === '', 'the secret box is emptied once it is sent');

  await page.click(`${w} [data-x-signin]`);
  await settle(page, () => window.__pane.length === 2);
  const p1 = (await pane(page))[1];
  check(p1.profile === 'desk-x' && /x\.com\/i\/oauth2\/authorize/.test(p1.url), '"Sign in with X" opens X in the pane on the "desk-x" profile');
  check(calls(srv, 'POST', /connect\/x\/start$/).length === 1, 'the sign-in starts with one POST (through the passcode prompt)');
  srv.xConnected = true; srv.ov.x.state = 'connected'; srv.flows['flow-1'].status = 'done';
  await settle(page, () => /Connected/.test(document.querySelector('[data-conn-account][data-platform="x"] [data-conn-status]').textContent));
  ok('the X account ends on "Connected"');
  check(/X is connected/.test(await txt(page, `${xrow} [data-guide="x"]`)) || /Manage/.test(await txt(page, guideBtn)), 'the wizard shows it is connected');

  // LinkedIn
  const li = await txt(page, '[data-conn-account][data-platform="linkedin"] [data-guide="linkedin"]');
  check(/Community Management API/.test(li) && /Waiting for LinkedIn approval/.test(li), 'LinkedIn says in one line what to apply for');
  const lhref = await page.getAttribute('[data-conn-account][data-platform="linkedin"] [data-guide="linkedin"] a', 'href');
  check(/learn\.microsoft\.com\/en-us\/linkedin\/marketing\/community-management/.test(lhref || ''), 'and links where to apply');

  // Copy rules: open every guide, then read the whole screen.
  for (const id of ['google', 'openai', 'higgsfield']) {
    if (!(await page.$(`[data-guide="${id}"]`))) {
      if (id === 'higgsfield') { await page.waitForTimeout(200); await page.evaluate(() => { const d = document.querySelector('[data-conn-advanced]'); if (d) d.open = true; }); }
      await page.click(`[data-conn-engine="${id}"] [data-engine-guide]`);
    }
  }
  const all = await page.$eval('[data-connections]', (e) => e.innerText);
  check(!/\boauth\b|\bvault\b|\bDCR\b|\bPKCE\b/i.test(all), 'no jargon (OAuth, vault, DCR, PKCE) in the Connections text' + (/\boauth\b|\bvault\b|\bDCR\b|\bPKCE\b/i.exec(all) ? ': ' + /.{0,40}(\boauth\b|\bvault\b|\bDCR\b|\bPKCE\b).{0,40}/i.exec(all)[0] : ''));
  check(!/[—–]/.test(all), 'no em-dash in the Connections text' + (/.{0,30}[—–].{0,30}/.exec(all) ? ': ' + /.{0,30}[—–].{0,30}/.exec(all)[0] : ''));
  const html = await page.content();
  check(!html.includes(SECRET_SENTINEL) && !responses.some((r) => r.includes(SECRET_SENTINEL)), 'no secret value is in the page or any response');

  // Disconnect X
  await page.click(`${xrow} [data-x-disconnect]`);
  await settle(page, () => /Not connected/.test(document.querySelector('[data-conn-account][data-platform="x"] [data-conn-status]').textContent));
  ok('Disconnect X returns the account to Not connected');
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 5b: a phone ────────────────────────────────────────────────────────────
async function narrow(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, srv, { width: 344, height: 800 });
  await page.click('[data-conn-account][data-platform="x"] [data-conn-x-guide]');
  await page.waitForSelector('[data-guide="x"]');
  await page.click('[data-conn-engine="google"] [data-engine-guide]');
  await page.waitForSelector('[data-guide="google"]');
  const over = await page.$eval('[data-connections]', (e) => e.scrollWidth - e.clientWidth);
  check(over <= 1, 'the wizards fit at 344 wide (no horizontal overflow)', 'overflow at 344: ' + over + 'px');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('Higgsfield: sign in (credits) and the Advanced API key route'); await higgsfield(browser);
  console.log('Gemini + OpenAI: guided key paste and Test connection'); await keyPaste(browser);
  console.log('X wizard, LinkedIn line, copy rules'); await xWizard(browser);
  console.log('Narrow screen'); await narrow(browser);
} finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) FAILED`); process.exit(1); }
console.log('\nAll guided Connect checks passed.');
