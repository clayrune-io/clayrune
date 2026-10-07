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
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOTS = process.env.SMOKE_SHOTS || '';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, bad_) => (cond ? ok(good) : fail(bad_ || good));

// Same shapes the real GET /api/desk/engines returns (mc/desk_engines.py ENGINES[*].credential).
const CRED = {
  higgsfield: { vault_entry: 'higgsfield', username_label: 'API key ID', entry_type: 'api_key_pair', username_required: true, secret_label: 'API key secret',
    hint: 'Create an API key in the Higgsfield console; it shows a key ID and a key secret.', url: 'https://console.higgsfield.ai' },
  google: { vault_entry: 'gemini-api', username_label: null, entry_type: 'api_key', username_required: false, secret_label: 'Gemini API key',
    hint: 'Create the key in Google AI Studio; billing must be on for the project or Veo is refused.', url: 'https://aistudio.google.com/apikey' },
  openai: { vault_entry: 'openai-api', username_label: null, entry_type: 'api_key', username_required: false, secret_label: 'OpenAI API key',
    hint: 'Create the key on platform.openai.com; a ChatGPT or Codex plan does not include API use.', url: 'https://platform.openai.com/api-keys' },
};
const engines = (srv) => [
  { id: 'higgsfield', label: 'Higgsfield', auth: { kind: 'key_id_secret', vault_entry: 'higgsfield' }, job_limit_usd: null, credential: CRED.higgsfield,
    connected: { ready: false, exists: false, vault_entry: 'higgsfield', reason: "no vault entry named 'higgsfield' (add it in the Vault)" },
    models: [{ model_id: 'kling', kind: 'video', label: 'Kling', status: 'stable', aspect_ratios: ['16:9'] }] },
  { id: 'google', label: 'Google', auth: { kind: 'api_key', vault_entry: 'gemini-api' }, job_limit_usd: 2, credential: CRED.google,
    connected: srv.googleReady ? { ready: true, exists: true, vault_entry: 'gemini-api', reason: null }
      : { ready: false, exists: false, vault_entry: 'gemini-api', reason: "no vault entry named 'gemini-api' (add it in the Vault)" },
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
async function openConnections(page, engineId) {
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('connections', {}));
  // One grid (2026-10-03): an engine is a tile once connected, an Add service entry until then.
  // Open the Add service panel (it lists the engines once they load), then the one asked for.
  await page.waitForSelector('[data-conn-add-tile]', { timeout: 8000 });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-add-pick^="engine:"], [data-conn-tile^="engine:"]', { timeout: 8000 });
  if (!engineId) return;
  const tile = `[data-conn-tile="engine:${engineId}"]`;
  await page.click((await page.$(tile)) ? tile : `[data-add-pick="engine:${engineId}"]`);
  await page.waitForSelector(`[data-conn-engine="${engineId}"]`, { timeout: 8000 });
}
// The engine rows no longer open the Secrets form: Connect is a guided flow with its
// own passcode-gated save (e0549996). So open the editor the way the old Connect/Edit
// buttons did, by vault name with the engine's `credential` spec as the preset.
const openEngineEditor = (page, id, create) => page.evaluate(([spec, c]) => window.openSecretEditor(spec.vault_entry, { ...spec, create: c }), [CRED[id], create]);
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });

async function higgsfield(browser) {
  const srv = { secrets: [], writes: [], googleReady: false };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page, 'higgsfield');
  const hs = await txt(page, '[data-conn-engine="higgsfield"] [data-engine-status]');
  check((await page.$('[data-conn-engine="higgsfield"] [data-engine-guide]')) !== null, 'a not-connected engine row has a Connect button (the guided flow)', 'no Connect on higgsfield');
  await openEngineEditor(page, 'higgsfield', true);
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-user-name') && document.getElementById('sec-user-name').textContent === 'API key ID');

  check((await page.inputValue('#sec-name')) === 'higgsfield' && (await page.$eval('#sec-name', (e) => e.readOnly)), 'the name is prefilled with the vault entry and locked', 'name field');
  check((await txt(page, '#sec-user-name')) === 'API key ID' && /required/.test(await txt(page, '#sec-user-opt')), 'username field is labelled "API key ID — required"', 'username label: ' + await txt(page, '#sec-user-label'));
  check((await txt(page, '#sec-value-name')) === 'API key secret', 'secret field is labelled "API key secret"', 'secret label: ' + await txt(page, '#sec-value-name'));
  const hint = await txt(page, '#sec-preset-hint');
  check(/Higgsfield console/.test(hint) && (await page.$eval('#sec-preset-hint a', (a) => a.href)).startsWith('https://console.higgsfield.ai'), 'the hint names where to get it and links the vendor', 'hint: ' + hint);
  check(!(await hidden(page, '#sec-user-block')), 'the username field is visible', 'username hidden');
  check((await steps(page)) === '1. 2. 3. 4. 5. 6. 7.', 'steps are numbered 1..7', 'steps: ' + await steps(page));
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
    await openEngineEditor(m.page, 'higgsfield', true);
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
  await openEngineEditor(page, 'google', true);
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Gemini API key');
  check(await hidden(page, '#sec-user-block'), 'the Gemini form has no username field', 'username block visible');
  check((await txt(page, '#sec-value-name')) === 'Gemini API key', 'secret field is labelled "Gemini API key"', 'label');
  check(/billing must be on/.test(await txt(page, '#sec-preset-hint')) && (await page.$eval('#sec-preset-hint a', (a) => a.href)).includes('aistudio.google.com'), 'hint says billing must be on and links AI Studio', 'hint: ' + await txt(page, '#sec-preset-hint'));
  check((await steps(page)) === '1. 2. 3. 4. 5. 6.', 'steps renumber to 1..6 with the field hidden', 'steps: ' + await steps(page));
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
  await openConnections(page, 'google');
  check((await page.$eval('[data-conn-engine="google"] [data-engine-guide]', (b) => b.textContent.trim())) === 'Replace key',
    'a connected engine offers Replace key, not Connect', 'google buttons');
  await openEngineEditor(page, 'google', false);
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Gemini API key');
  check((await page.$eval('#sec-name', (e) => e.readOnly)) && (await page.getAttribute('#sec-value', 'placeholder')) === 'unchanged', 'Edit opens an edit of the existing entry (name locked, value unchanged)', 'edit form');
  await page.evaluate(() => closeModalById('__secret-edit'));

  // The same preset applies when the editor is opened from the Secrets list by name.
  await page.evaluate(() => window.openSecretEditor('gemini-api'));
  await page.waitForSelector(form, { timeout: 8000 });
  check((await txt(page, '#sec-value-name')) === 'Gemini API key' && await hidden(page, '#sec-user-block'), 'editing the vault entry from the Vault list uses the engine labels too', 'list edit labels');
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
  check(s.user === '3. Username — optional' && s.val === 'Password' && s.ph === 'ron@example.com' && s.twofa && s.hint && s.steps === '1. 2. 3. 4. 5. 6. 7.',
    'a plain Add is the stock form: "3. Username — optional" / Password / 2FA note', 'stock: ' + JSON.stringify(s));
  await page.fill('#sec-name', 'openai-api');
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'OpenAI API key');
  check(await hidden(page, '#sec-user-block') && /ChatGPT or Codex plan/.test(await txt(page, '#sec-preset-hint')), 'typing a known vault name (openai-api) applies the OpenAI preset', 'openai preset');
  await page.fill('#sec-name', 'higgsfield');
  await settle(page, () => document.getElementById('sec-user-name').textContent === 'API key ID');
  check(!(await hidden(page, '#sec-user-block')), 'typing higgsfield switches to the Higgsfield preset', 'higgsfield preset');
  await page.fill('#sec-name', 'reddit.password');
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Password');
  s = await stock();
  check(s.user === '3. Username — optional' && s.ph === 'ron@example.com' && s.twofa && s.steps === '1. 2. 3. 4. 5. 6. 7.', 'any other name goes back to the stock form', 'reverted: ' + JSON.stringify(s));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── Entry types (Ron 2026-10-01): the form opens with a Type step; each type shows only its own fields ──
const radioChecked = (page) => page.$eval(`${form} input[name="sec-type"]:checked`, (e) => e.value).catch(() => null);
const radiosDisabled = (page) => page.$$eval(`${form} input[name="sec-type"]`, (els) => els.every((e) => e.disabled));
const pickType = async (page, t) => { await page.click(`${form} input[name="sec-type"][value="${t}"]`); };
const shape = async (page) => ({
  user: !(await hidden(page, '#sec-user-block')), twofa: !(await hidden(page, '#sec-2fa-help')),
  userLabel: await txt(page, '#sec-user-name'), valueLabel: await txt(page, '#sec-value-name'),
  userVal: await page.inputValue('#sec-user'),
});

async function types(browser) {
  const srv = { secrets: [], writes: [], googleReady: false };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await page.evaluate(() => window.openSecretEditor(null));
  await page.waitForSelector(form, { timeout: 8000 });
  const labels = await page.$$eval(`${form} #sec-type-row label`, (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  check(JSON.stringify(labels) === JSON.stringify(['Login', 'API key', 'API key pair', 'Token']), 'the form offers Login / API key / API key pair / Token', 'chips: ' + labels);
  check((await page.$eval(`${form} .sec-step`, (e) => e.parentElement.textContent.replace(/\s+/g, ' ').trim())) === '1. Type', 'Type is the first step', 'first step');
  check((await radioChecked(page)) === 'login', 'a new entry starts as a Login', 'default type: ' + await radioChecked(page));
  let s = await shape(page);
  check(s.user && s.twofa && s.userLabel === 'Username' && s.valueLabel === 'Password', 'Login: Username + Password + the 2FA note', 'login: ' + JSON.stringify(s));
  const shot = async (p, name) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: resolve(SHOTS, name) }); } };
  await shot(page, 'type-login-1440.png');

  await page.fill('#sec-user', 'typed-under-login');
  await pickType(page, 'api_key');
  s = await shape(page);
  check(!s.user && !s.twofa && s.valueLabel === 'API key' && s.userVal === '', 'API key: one field "API key", no username, no 2FA, the typed username is cleared', 'api_key: ' + JSON.stringify(s));
  check((await steps(page)) === '1. 2. 3. 4. 5. 6.', 'steps renumber with the username hidden', 'steps: ' + await steps(page));
  await shot(page, 'type-api-key-1440.png');
  await page.fill('#sec-name', 'openai.api-key'); await page.fill('#sec-value', 'sk-1');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  check(srv.writes.length === 1 && srv.writes[0].body.entry_type === 'api_key' && srv.writes[0].body.username === '' && srv.writes[0].body.value === 'sk-1',
    'saving an API key sends entry_type=api_key and no username', 'write: ' + JSON.stringify(srv.writes));

  await page.evaluate(() => window.openSecretEditor(null));
  await page.waitForSelector(form, { timeout: 8000 });
  await pickType(page, 'api_key_pair');
  s = await shape(page);
  check(s.user && !s.twofa && s.userLabel === 'Key ID' && s.valueLabel === 'Key secret' && /required/.test(await txt(page, '#sec-user-opt')), 'API key pair: "Key ID — required" + "Key secret", no 2FA', 'pair: ' + JSON.stringify(s));
  await shot(page, 'type-api-key-pair-1440.png');
  await page.fill('#sec-name', 'acme.key'); await page.fill('#sec-value', 'sec-1');
  await page.click('#sec-save');
  await settle(page, () => /Enter the Key ID/.test(document.getElementById('sec-status').textContent));
  check(srv.writes.length === 1, 'a pair without a Key ID is refused in the page', 'writes: ' + srv.writes.length);
  await page.fill('#sec-user', 'kid-7');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  check(srv.writes.length === 2 && srv.writes[1].body.entry_type === 'api_key_pair' && srv.writes[1].body.username === 'kid-7',
    'saving a pair sends entry_type=api_key_pair with the Key ID in the username slot', 'write: ' + JSON.stringify(srv.writes[1]));

  await page.evaluate(() => window.openSecretEditor(null));
  await page.waitForSelector(form, { timeout: 8000 });
  await pickType(page, 'token');
  s = await shape(page);
  check(!s.user && !s.twofa && s.valueLabel === 'Token', 'Token: one field "Token", no username, no 2FA', 'token: ' + JSON.stringify(s));
  await shot(page, 'type-token-1440.png');
  await pickType(page, 'login');
  await page.fill('#sec-user', 'x');
  await pickType(page, 'token'); await pickType(page, 'login');   // the username does not survive a trip through a type without one
  s = await shape(page);
  check(s.user && s.twofa && s.valueLabel === 'Password' && s.userVal === '', 'switching back to Login restores its fields, empty', 'back: ' + JSON.stringify(s));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function typesLegacyAndPresets(browser) {
  const srv = { googleReady: true, writes: [], secrets: [
    { name: 'old.login', username: 'u/ron', kind: 'password', entry_type: 'login', entry_type_inferred: true, scope: 'global', allow_unattended: true, description: '' },
    { name: 'old.key', username: '', kind: 'password', entry_type: 'api_key', entry_type_inferred: true, scope: 'global', allow_unattended: true, description: '' },
    { name: 'tok', username: '', kind: 'password', entry_type: 'token', entry_type_inferred: false, scope: 'global', allow_unattended: true, description: '' },
    { name: 'higgsfield', username: 'kid-1', kind: 'password', entry_type: 'login', entry_type_inferred: true, scope: 'global', allow_unattended: true, description: '' },
    { name: 'gemini-api', username: '', kind: 'password', entry_type: 'api_key', entry_type_inferred: false, scope: 'global', allow_unattended: true, description: '' },
  ] };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);   // loads the engine specs the preset lookup reads
  await page.evaluate(() => window.openSecretsVault());
  await page.waitForSelector('#secrets-list [data-sec-type]', { timeout: 8000 });
  const tags = await page.$$eval('#secrets-list [data-sec-type]', (els) => els.map((e) => e.closest('div[style*="flex:1"]').querySelector('code').textContent + '=' + e.textContent.trim()));
  check(JSON.stringify(tags) === JSON.stringify(['old.login=Login', 'old.key=API key', 'tok=Token', 'higgsfield=API key pair', 'gemini-api=API key']),
    'each list row carries a type tag (an engine entry saved before types shows its engine type)', 'tags: ' + JSON.stringify(tags));
  await page.evaluate(() => closeModalById('__secrets'));

  await page.evaluate(() => window.openSecretEditor('old.login'));
  await page.waitForSelector(form, { timeout: 8000 });
  let s = await shape(page);
  check((await radioChecked(page)) === 'login' && s.user && s.userVal === 'u/ron' && s.twofa, 'a legacy entry with a username opens as Login, username intact', 'legacy login: ' + JSON.stringify(s) + await radioChecked(page));
  await page.evaluate(() => closeModalById('__secret-edit'));
  await page.evaluate(() => window.openSecretEditor('old.key'));
  await page.waitForSelector(form, { timeout: 8000 });
  s = await shape(page);
  check((await radioChecked(page)) === 'api_key' && !s.user && !s.twofa, 'a legacy entry with no username opens as API key', 'legacy key: ' + JSON.stringify(s));
  await page.fill('#sec-desc', 'edited');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  check(srv.writes.length === 1 && srv.writes[0].method === 'PATCH' && srv.writes[0].body.entry_type === 'api_key' && srv.writes[0].body.username === '',
    'editing sends the type the form showed (PATCH)', 'write: ' + JSON.stringify(srv.writes));

  await page.evaluate(() => window.openSecretEditor('higgsfield'));
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-user-name').textContent === 'API key ID');
  s = await shape(page);
  check((await radioChecked(page)) === 'api_key_pair' && (await radiosDisabled(page)) && s.user && s.valueLabel === 'API key secret' && s.userVal === 'kid-1',
    'a preset locks the type: higgsfield = API key pair with its own "API key ID" / "API key secret" labels', 'higgsfield: ' + JSON.stringify(s));
  await page.evaluate(() => closeModalById('__secret-edit'));
  await page.evaluate(() => window.openSecretEditor('gemini-api'));
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-value-name').textContent === 'Gemini API key');
  check((await radioChecked(page)) === 'api_key' && (await radiosDisabled(page)) && (await hidden(page, '#sec-user-block')), 'gemini-api = API key, locked, no username', 'gemini');
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();

  if (SHOTS) {
    for (const t of ['login', 'api_key', 'api_key_pair', 'token']) {
      const m = await newPage(browser, { secrets: [], writes: [], googleReady: false }, { width: 390, height: 844 });
      await m.page.evaluate(() => window.openSecretEditor(null));
      await m.page.waitForSelector(form, { timeout: 8000 });
      await pickType(m.page, t);
      await m.page.screenshot({ path: resolve(SHOTS, `type-${t.replace(/_/g, '-')}-390.png`) });
      await m.ctx.close();
    }
  }
}

// An engine preset must not outlive the editor it locked: Edit higgsfield, Cancel, Edit a Token -> the
// Token form opens as Token (not the type held before the preset), and Save sends entry_type=token.
async function stalePreset(browser) {
  const srv = { googleReady: true, writes: [], secrets: [
    { name: 'higgsfield', username: 'kid-1', kind: 'password', entry_type: 'api_key_pair', entry_type_inferred: false, scope: 'global', allow_unattended: true, description: '' },
    { name: 'tok', username: '', kind: 'password', entry_type: 'token', entry_type_inferred: false, scope: 'global', allow_unattended: true, description: '' },
  ] };
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 900 });
  await openConnections(page);   // loads the engine specs the preset lookup reads
  await page.evaluate(() => window.openSecretEditor('higgsfield'));
  await page.waitForSelector(form, { timeout: 8000 });
  await settle(page, () => document.getElementById('sec-user-name').textContent === 'API key ID');
  check((await radioChecked(page)) === 'api_key_pair' && (await radiosDisabled(page)), 'higgsfield opens locked as API key pair', 'higgsfield: ' + await radioChecked(page));
  await page.click(`${form} .btn-secondary`);   // Cancel
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  await page.evaluate(() => window.openSecretEditor('tok'));
  await page.waitForSelector(form, { timeout: 8000 });
  const s = await shape(page);
  check((await radioChecked(page)) === 'token' && !(await radiosDisabled(page)) && !s.user && s.valueLabel === 'Token',
    'a Token edit after a preset edit opens as Token, unlocked, no username', 'token after preset: ' + JSON.stringify(s) + await radioChecked(page));
  await page.fill('#sec-desc', 'edited');
  await page.click('#sec-save');
  await settle(page, () => !document.querySelector('.modal-window[data-modal-id="__secret-edit"]'));
  check(srv.writes.length === 1 && srv.writes[0].method === 'PATCH' && srv.writes[0].body.entry_type === 'token' && srv.writes[0].body.username === '',
    'saving it sends entry_type=token, not the stale pre-preset type', 'write: ' + JSON.stringify(srv.writes));
  pageErrors.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('1. Higgsfield Connect'); await higgsfield(browser);
  console.log('2. Gemini Connect'); await gemini(browser);
  console.log('3. Edit'); await edit(browser);
  console.log('4. Plain Add'); await plainAdd(browser);
  console.log('5. Entry types'); await types(browser);
  console.log('6. Legacy entries + engine presets'); await typesLegacyAndPresets(browser);
  console.log('7. A preset does not outlive its editor'); await stalePreset(browser);
} finally { await browser.close(); }
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
