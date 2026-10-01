#!/usr/bin/env node
/**
 * Secrets form labels follow the credential type (Ron 2026-10-01). Against a fake server (no vendor,
 * no vault is touched), `desk_v1_live` ON. The engine spec the page reads is
 * GET /api/desk/engines -> `credential` (pinned against the real ENGINES by tests/test_desk_engines.py).
 *   1. Higgsfield Connect -> the Secrets form opens with the name prefilled and locked, the labels
 *                            "API key ID" (required) + "API key secret", the vendor hint + link; an empty key
 *                            ID is refused in the page; the save is ONE POST through the passcode prompt.
 *   2. Gemini Connect     -> no username field at all, "Gemini API key", billing hint; saves username "".
 *   3. Edit               -> a connected engine's Edit opens the form as an edit (PATCH), same labels.
 *   4. Plain Add          -> stock form (2. Username - optional / Value) until a known vault name is typed
 *                            (openai-api -> OpenAI labels), and back to stock for any other name.
 *
 * RUN   cd tools/smoke && node secrets-engine-labels.mjs
 *       SMOKE_SHOTS=<dir> also writes the Higgsfield form at 1440px and 390px.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
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
const SHOTS = process.env.SMOKE_SHOTS || '';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, bad_) => (cond ? ok(good) : fail(bad_ || good));

// Same shapes the real GET /api/desk/engines returns (mc/desk_engines.py ENGINES[*].credential).
const CRED = {
  higgsfield: { vault_entry: 'higgsfield', username_label: 'API key ID', username_required: true, secret_label: 'API key secret',
    hint: 'Create an API key in the Higgsfield console; it shows a key ID and a key secret.', url: 'https://console.higgsfield.ai' },
  google: { vault_entry: 'gemini-api', username_label: null, username_required: false, secret_label: 'Gemini API key',
    hint: 'Create the key in Google AI Studio; billing must be on for the project or Veo is refused.', url: 'https://aistudio.google.com/apikey' },
  openai: { vault_entry: 'openai-api', username_label: null, username_required: false, secret_label: 'OpenAI API key',
    hint: 'Create the key on platform.openai.com; a ChatGPT or Codex plan does not include API use.', url: 'https://platform.openai.com/api-keys' },
};
const engines = (srv) => [
  { id: 'higgsfield', label: 'Higgsfield', auth: { kind: 'key_id_secret', vault_entry: 'higgsfield' }, job_limit_usd: null, credential: CRED.higgsfield,
    connected: { ready: false, exists: false, vault_entry: 'higgsfield', reason: "no vault entry named 'higgsfield' (add it in Secrets)" },
    models: [{ model_id: 'kling', kind: 'video', label: 'Kling', status: 'stable', aspect_ratios: ['16:9'] }] },
  { id: 'google', label: 'Google', auth: { kind: 'api_key', vault_entry: 'gemini-api' }, job_limit_usd: 2, credential: CRED.google,
    connected: srv.googleReady ? { ready: true, exists: true, vault_entry: 'gemini-api', reason: null }
      : { ready: false, exists: false, vault_entry: 'gemini-api', reason: "no vault entry named 'gemini-api' (add it in Secrets)" },
    models: [{ model_id: 'veo', kind: 'video', label: 'Veo', status: 'preview', aspect_ratios: ['16:9'] }] },
  { id: 'openai', label: 'OpenAI', auth: { kind: 'api_key', vault_entry: 'openai-api' }, job_limit_usd: 5, credential: CRED.openai,
    connected: { ready: true, exists: true, vault_entry: 'openai-api', reason: null },
    models: [{ model_id: 'gpt-image', kind: 'image', label: 'GPT image', status: 'stable', aspect_ratios: ['1:1'] }] },
];

async function newPage(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(fx.projects.map((p) => ({
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
    if (path === '/api/secrets' && method === 'GET') return J({ secrets: srv.secrets });
    if (path === '/api/secrets/vault-lock') return J({ state: 'unlocked' });
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/secrets' && method === 'POST') { srv.writes.push({ method, path, body }); return J({ ...body, value: undefined }, 201); }
    if (path.startsWith('/api/secrets/') && method === 'PATCH') { srv.writes.push({ method, path, body }); return J({ ...body, value: undefined }); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    if (path === '/api/desk/workspace') return J({ ...workspaceFromFixtures(fx), projects, pieces: [] });
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: engines(srv) });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => {
    window.__proofs = [];
    window.humanProofFetch = async (url, init, proof) => {
      window.__proofs.push({ url, title: proof && proof.title });
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
  });
  return { ctx, page, pageErrors };
}

const txt = (page, sel) => page.textContent(sel).then((t) => (t || '').replace(/\s+/g, ' ').trim()).catch(() => '');
const form = '.modal-window[data-modal-id="__secret-edit"]';
const hidden = (page, sel) => page.$eval(sel, (el) => el.hidden || el.offsetParent === null).catch(() => true);
const steps = (page) => page.$$eval(`${form} .sec-step`, (els) => els.filter((e) => e.offsetParent !== null).map((e) => e.textContent.trim()).join(' '));
async function openConnections(page) {
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('connections', {}));
  await page.waitForSelector('[data-conn-engine="higgsfield"]', { timeout: 8000 });
}
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });

async function higgsfield(browser) {
  const srv = { secrets: [], writes: [], googleReady: false };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);
  const hs = await txt(page, '[data-conn-engine="higgsfield"] [data-engine-status]');
  check((await page.$('[data-conn-engine="higgsfield"] [data-engine-connect]')) !== null, 'a not-connected engine row has a Connect button', 'no Connect on higgsfield');
  await page.click('[data-conn-engine="higgsfield"] [data-engine-connect]');
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-user-name') && document.getElementById('sec-user-name').textContent === 'API key ID');

  check((await page.inputValue('#sec-name')) === 'higgsfield' && (await page.$eval('#sec-name', (e) => e.readOnly)), 'the name is prefilled with the vault entry and locked', 'name field');
  check((await txt(page, '#sec-user-name')) === 'API key ID' && /required/.test(await txt(page, '#sec-user-opt')), 'username field is labelled "API key ID — required"', 'username label: ' + await txt(page, '#sec-user-label'));
  check((await txt(page, '#sec-value-name')) === 'API key secret', 'secret field is labelled "API key secret"', 'secret label: ' + await txt(page, '#sec-value-name'));
  const hint = await txt(page, '#sec-preset-hint');
  check(/Higgsfield console/.test(hint) && (await page.$eval('#sec-preset-hint a', (a) => a.href)).startsWith('https://console.higgsfield.ai'), 'the hint names where to get it and links the vendor', 'hint: ' + hint);
  check(!(await hidden(page, '#sec-user-block')), 'the username field is visible', 'username hidden');
  check((await steps(page)) === '1. 2. 3. 4. 5. 6.', 'steps are numbered 1..6', 'steps: ' + await steps(page));
  if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: resolve(SHOTS, 'higgsfield-1440.png') }); }

  await page.fill('#sec-value', 'secret-value');
  await page.click('#sec-save');
  await settle(page, () => /Enter the API key ID/.test(document.getElementById('sec-status').textContent));
  check(srv.writes.length === 0, 'a missing key ID is refused in the page, nothing sent', 'writes: ' + JSON.stringify(srv.writes));
  await page.fill('#sec-user', 'kid-123');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  const pf = await page.evaluate(() => window.__proofs);
  check(srv.writes.length === 1 && srv.writes[0].method === 'POST' && srv.writes[0].body.name === 'higgsfield'
    && srv.writes[0].body.username === 'kid-123' && srv.writes[0].body.value === 'secret-value' && pf.length === 1 && /Save secret/.test(pf[0].title),
    'save is ONE POST through the passcode prompt: username=key ID, value=secret', 'write: ' + JSON.stringify({ w: srv.writes, pf }));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();

  if (SHOTS) {
    const m = await newPage(browser, srv, { width: 390, height: 844 });
    await openConnections(m.page);
    await m.page.click('[data-conn-engine="higgsfield"] [data-engine-connect]');
    await m.page.waitForSelector(form, { timeout: 8000 });
    await settle(m.page, () => document.getElementById('sec-user-name').textContent === 'API key ID');
    await m.page.screenshot({ path: resolve(SHOTS, 'higgsfield-390.png') });
    await m.ctx.close();
  }
}

async function gemini(browser) {
  const srv = { secrets: [], writes: [], googleReady: false };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);
  await page.click('[data-conn-engine="google"] [data-engine-connect]');
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Gemini API key');
  check(await hidden(page, '#sec-user-block'), 'the Gemini form has no username field', 'username block visible');
  check((await txt(page, '#sec-value-name')) === 'Gemini API key', 'secret field is labelled "Gemini API key"', 'label');
  check(/billing must be on/.test(await txt(page, '#sec-preset-hint')) && (await page.$eval('#sec-preset-hint a', (a) => a.href)).includes('aistudio.google.com'), 'hint says billing must be on and links AI Studio', 'hint: ' + await txt(page, '#sec-preset-hint'));
  check((await steps(page)) === '1. 2. 3. 4. 5.', 'steps renumber to 1..5 with the field hidden', 'steps: ' + await steps(page));
  await page.evaluate(() => { document.getElementById('sec-user').value = 'stale-typed'; });   // hidden input must not leak into the save
  await page.fill('#sec-value', 'g-key');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  check(srv.writes.length === 1 && srv.writes[0].body.name === 'gemini-api' && srv.writes[0].body.username === '' && srv.writes[0].body.value === 'g-key',
    'saves as gemini-api with no username', 'write: ' + JSON.stringify(srv.writes));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function edit(browser) {
  const srv = { secrets: [{ name: 'gemini-api', username: '', scope: 'global', allow_unattended: true, description: '' }], writes: [], googleReady: true };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);
  check((await page.$('[data-conn-engine="google"] [data-engine-connect]')) === null && (await page.$('[data-conn-engine="google"] [data-engine-edit]')) !== null,
    'a connected engine shows Edit, not Connect', 'google buttons');
  await page.click('[data-conn-engine="google"] [data-engine-edit]');
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Gemini API key');
  check((await page.$eval('#sec-name', (e) => e.readOnly)) && (await page.getAttribute('#sec-value', 'placeholder')) === 'unchanged', 'Edit opens an edit of the existing entry (name locked, value unchanged)', 'edit form');
  await page.evaluate(() => closeModalById('__secret-edit'));

  // The same preset applies when the editor is opened from the Secrets list by name.
  await page.evaluate(() => window.openSecretEditor('gemini-api'));
  await page.waitForSelector(form, { timeout: 8000 });
  check((await txt(page, '#sec-value-name')) === 'Gemini API key' && await hidden(page, '#sec-user-block'), 'editing the vault entry from the Secrets list uses the engine labels too', 'list edit labels');
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function plainAdd(browser) {
  const srv = { secrets: [], writes: [], googleReady: false };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);   // loads the engine specs the preset lookup reads
  await page.evaluate(() => window.openSecretEditor(null));
  await page.waitForSelector(form, { timeout: 8000 });
  const stock = async () => ({ user: await txt(page, '#sec-user-label'), val: await txt(page, '#sec-value-name'), ph: await page.getAttribute('#sec-user', 'placeholder'),
    twofa: !(await hidden(page, '#sec-2fa-help')), hint: await hidden(page, '#sec-preset-hint'), steps: await steps(page) });
  let s = await stock();
  check(s.user === '2. Username — optional' && s.val === 'Value' && s.ph === 'ron@example.com' && s.twofa && s.hint && s.steps === '1. 2. 3. 4. 5. 6.',
    'a plain Add is the stock form: "2. Username — optional" / Value / 2FA note', 'stock: ' + JSON.stringify(s));
  await page.fill('#sec-name', 'openai-api');
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'OpenAI API key');
  check(await hidden(page, '#sec-user-block') && /ChatGPT or Codex plan/.test(await txt(page, '#sec-preset-hint')), 'typing a known vault name (openai-api) applies the OpenAI preset', 'openai preset');
  await page.fill('#sec-name', 'higgsfield');
  await settle(page, () => document.getElementById('sec-user-name').textContent === 'API key ID');
  check(!(await hidden(page, '#sec-user-block')), 'typing higgsfield switches to the Higgsfield preset', 'higgsfield preset');
  await page.fill('#sec-name', 'reddit.password');
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Value');
  s = await stock();
  check(s.user === '2. Username — optional' && s.ph === 'ron@example.com' && s.twofa && s.steps === '1. 2. 3. 4. 5. 6.', 'any other name goes back to the stock form', 'reverted: ' + JSON.stringify(s));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('1. Higgsfield Connect'); await higgsfield(browser);
  console.log('2. Gemini Connect'); await gemini(browser);
  console.log('3. Edit'); await edit(browser);
  console.log('4. Plain Add'); await plainAdd(browser);
} finally { await browser.close(); }
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
