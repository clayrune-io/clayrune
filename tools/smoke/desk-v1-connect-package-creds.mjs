#!/usr/bin/env node
/**
 * Desk v1 — the credential rows of the PACKAGE setup page (static/js/desk-v1-connect-package-creds.js), against a fake
 * server at 1440 and 390 wide. The case is the one Ron reported: a GitHub MCP (YouTube) whose README names YOUTUBE_API_KEY
 * and a person with no YouTube key saved.
 *
 *   1. Layout    inputs and the select are single-line height, Remove is an inline button, nothing overflows sideways.
 *   2. Name      a README-named variable is fixed text, not a box; a row the person adds keeps its editable name.
 *   3. Copy      one plain sentence per field; the old three-sentence block is gone.
 *   4. Choose    a saved credential is a dropdown of existing names (metadata); picking one writes nothing.
 *   5. Add new   a masked paste box; the value is written ONCE, by POST /api/secrets through the passcode prompt, only
 *                when Continue is pressed (Review refuses a Vault name that does not exist); a cancelled or wrong
 *                passcode keeps the pasted value and writes nothing; an existing name is never replaced.
 *   6. No leak   the value reaches no request except that one write, and is in no markup or storage after Save.
 *
 * Screenshots: docs/desk_v1/screens/connect_package_creds_*_{1440,390}.png (CREDS_SHOT_DIR overrides)
 *
 * RUN   cd tools/smoke && node desk-v1-connect-package-creds.mjs
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
const SHOT_DIR = process.env.CREDS_SHOT_DIR || resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));
const PASSCODE = 'right-passcode';
const SECRET = 'AIza-fixture-only-in-the-paste-box-4417';

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
got = resolve.resolve('youtube.com', own_hosts=('mc.smoke.test',))
svc = got.pop('service') or registry.lookup(got['host'])
print(json.dumps({'youtube.com': {**got, **type_view.project_service(svc['id'] if svc else None)}}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, secrets: [{ name: 'existing.key', scope: 'global', entry_type: 'api_key', allow_unattended: true }], secretWrites: [] };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = []; page.setDefaultTimeout(8000);
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
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
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/secrets' && method === 'GET') { srv.log.push({ method, path, body: null }); return J({ secrets: srv.secrets }); }
    if (path === '/api/secrets' && method === 'POST') {
      srv.log.push({ method, path, body }); srv.secretWrites.push(body);
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.secrets.push({ name: body.name, scope: body.scope, entry_type: body.entry_type, allow_unattended: body.allow_unattended });
      return J({ name: body.name, scope: body.scope });
    }
    if (!path.startsWith('/api/desk/') && !path.startsWith('/api/browser/')) return route.abort();
    srv.log.push({ method, path, body });
    if (path === '/api/desk/connect/custom/github/stage') return J({ stage_id: 'github-fixture', sha: 'a'.repeat(40), command: 'node', args: ['dist/cli.js'], credentials: [{ env: 'YOUTUBE_API_KEY', source: 'README.md' }], optional_credentials: [{ env: 'YOUTUBE_API_KEY2', source: 'README.md' }], build_required: true });
    if (path === '/api/desk/connect/custom/review') return J({ fingerprint: 'sha256:fixture', request_id: 'custom-fixture', title: 'youtube-mcp-server', server_name: 'youtube-mcp-server', protocol: 'stdio', command: { command: 'node', args: ['/smoke/mcp/transport.js'] }, package: { version: 'a'.repeat(40), pinned: true, registry: 'GitHub', integrity: 'sha512:fixture', source: 'https://github.com/ZubeidHendricks/youtube-mcp-server.git' }, reach: { who: 'One project', scope: 'project', local_code: 'Runs with your file and network access.' }, credentials: body.credentials || [], scripts: [], dependencies: [], risks: [], limitations: [], changes: [] });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines') return J({ engines: [] });
    if (path === '/api/desk/services') return J([]);
    if (path === '/api/desk/accounts') return J([]);
    if (path === '/api/desk/connect/custom/connections') return J({ connections: [] });
    if (path === '/api/desk/connect/suggest') return J({ suggestions: [] });
    if (path === '/api/desk/connect/types') return TYPES[body.input] ? J(TYPES[body.input]) : J({ error: 'Unknown service name.' }, 400);
    if (path === '/api/browser/agent-read') return J({ policies: {}, profiles: [] });
    if (path === '/api/browser/profiles') return J({ profiles: [] });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const primary = (p) => p.click('[data-cfw-primary]');
const step = (p, s) => p.waitForSelector(`[data-cfw-step="${s}"]`);
const passcode = async (p, code) => { await p.waitForSelector('input[id^="hp-passcode-"]'); await p.fill('input[id^="hp-passcode-"]', code); await p.click('.modal-content .btn-add'); };
const toDetails = async (p) => {
  await p.click('[data-conn-add-tile]'); await p.fill('[data-cfw-input]', 'youtube.com'); await primary(p); await step(p, 'connection');
  await p.click('[data-cfw-details] > summary'); await p.check('[data-cfw-type][value="custom-npm"]'); await primary(p);
  await p.fill('[data-pk-package]', 'https://github.com/ZubeidHendricks/youtube-mcp-server'); await primary(p);
  await p.waitForSelector('[data-pk-command-input]'); await p.waitForSelector('[data-pk-cred="0"]');
  await p.waitForFunction(() => !!document.querySelector('[data-pk-cred-vault="0"] option[value="existing.key"]'));
};
const boxes = (p, sel) => p.$$eval(sel, (els) => els.map((e) => { const r = e.getBoundingClientRect(); return { h: Math.round(r.height), w: Math.round(r.width), right: Math.round(r.right) }; }));
const sentRequests = (srv) => JSON.stringify(srv.log);

const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    console.log(`\n== ${width}px: GitHub MCP whose README names YOUTUBE_API_KEY ==`);
    const srv = makeServer();
    const { ctx, page: p, pageErrors } = await newPage(browser, { srv, width, height: width === 390 ? 844 : 1000 });
    await toDetails(p);

    // 1 + 2: layout and name
    const fixed = await p.textContent('[data-pk-cred-fixed="0"]');
    check(fixed === 'YOUTUBE_API_KEY' && await p.locator('[data-pk-cred-env]').count() === 0, 'a README-named variable is fixed text, not a box', `fixed=${fixed}`);
    const fields = await boxes(p, '[data-pk-cred] select, [data-pk-cred] input');
    check(fields.length >= 1 && fields.every((f) => f.h <= 56), 'every credential field is single-line height', JSON.stringify(fields));
    const btns = await boxes(p, '[data-pk-cred] button');
    check(btns.length >= 2 && btns.every((b) => b.h <= 56), 'Add a new credential and Remove are normal inline buttons', JSON.stringify(btns));
    const overflow = await p.evaluate(() => { const b = document.querySelector('[data-cfw-body]'); return b.scrollWidth - b.clientWidth; });
    check(overflow <= 1, 'the credential rows do not scroll sideways', `overflow ${overflow}px`);

    // 3: copy
    const block = await p.textContent('[data-pk-creds]');
    check(!/if it is not saved yet|Enter the name of an existing saved credential|Add a required credential name/.test(block), 'the three repeated helper sentences are gone', block);
    const helpers = await p.$$eval('[data-pk-cred="0"] .desk-v1-cfw-fact-text', (n) => n.map((e) => e.textContent.trim()));
    check(helpers.length === 1 && helpers[0] === 'Choose a saved credential, or add a new one.', 'one plain sentence under the credential field', JSON.stringify(helpers));
    check((await p.textContent('[data-pk-creds-optional]')).includes('YOUTUBE_API_KEY2'), 'numbered fallback keys stay a single optional line');
    await p.evaluate(() => document.querySelector('[data-pk-creds]').scrollIntoView({ block: 'center' }));
    await p.screenshot({ path: resolve(SHOT_DIR, `connect_package_creds_github_${width}.png`), fullPage: true });

    // 4: Continue with nothing chosen stops
    await primary(p);
    await p.waitForFunction(() => /Choose a saved credential or paste a value/.test(document.body.innerText));
    check(srv.secretWrites.length === 0 && await p.locator('[data-cfw-step="setup"]').count() === 1, 'Continue with no credential chosen stays put and writes nothing');

    // 5: Add new
    await p.click('[data-pk-cred-new="0"]');
    await p.waitForSelector('[data-pk-cred-secret]');
    check(await p.getAttribute('[data-pk-cred-secret]', 'type') === 'password', 'the new value goes in a masked field');
    check(await p.locator('[data-pk-cred-vault]').count() === 0, 'the saved-credential dropdown gives way to the paste box');
    const addNewText = await p.textContent('[data-pk-creds]');
    check(!/exact|match|such as YOUTUBE_API_KEY/i.test(addNewText) && await p.locator('[data-pk-cred] input[type="text"]').count() === 0, 'Add new asks for the value only: no entry name to type, no instruction to match names', addNewText);
    const sb = await boxes(p, '[data-pk-cred] input');
    check(sb.every((f) => f.h <= 56), 'the paste box is single-line height', JSON.stringify(sb));
    await p.fill('[data-pk-cred-secret]', SECRET);
    await p.evaluate(() => document.querySelector('[data-pk-creds]').scrollIntoView({ block: 'center' }));
    await p.screenshot({ path: resolve(SHOT_DIR, `connect_package_creds_new_${width}.png`), fullPage: true });
    await primary(p);
    await p.waitForSelector('input[id^="hp-passcode-"]');
    check(srv.secretWrites.length === 0, 'nothing is written before the passcode');
    await p.click('.modal-content .btn-secondary'); await p.waitForTimeout(150);
    check(srv.secretWrites.length === 0 && await p.inputValue('[data-pk-cred-secret]') === SECRET, 'cancelling the passcode writes nothing and keeps the pasted value');
    await primary(p); await passcode(p, 'wrong-passcode');
    await p.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText));
    check(await p.locator('[data-cfw-step="setup"]').count() === 1 && await p.inputValue('[data-pk-cred-secret]') === SECRET, 'a wrong passcode keeps the pasted value and stays on this page');
    srv.secretWrites.length = 0;
    await passcode(p, PASSCODE);                       // the prompt stays open after a wrong passcode
    await step(p, 'permissions');
    const w = srv.secretWrites;
    check(w.length === 1 && w[0].name === 'youtube_api_key' && w[0].value === SECRET && w[0].scope === 'global' && w[0].entry_type === 'api_key',
      'Continue writes one global api_key entry named from the variable', JSON.stringify(w.map((x) => ({ ...x, value: '…', passcode: '…' }))));
    check(!srv.log.some((r) => r.path.startsWith('/api/desk/') && JSON.stringify(r.body || {}).includes(SECRET)), 'the value reaches no Desk request');

    // Review carries the name only
    await p.selectOption('[data-cfp-project]', await p.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value));
    await primary(p); await step(p, 'review');
    await p.waitForSelector('[data-pk-credential]', { state: 'attached' });
    const review = srv.log.filter((r) => r.path === '/api/desk/connect/custom/review').pop().body;
    check(JSON.stringify(review.credentials) === JSON.stringify([{ env: 'YOUTUBE_API_KEY', vault: 'youtube_api_key' }]), 'Review is asked for the variable and the Vault name only', JSON.stringify(review.credentials));
    check(await p.evaluate((s) => !document.documentElement.outerHTML.includes(s) && ![...document.querySelectorAll('input,textarea')].some((i) => i.value.includes(s)) && !JSON.stringify(localStorage).includes(s) && !JSON.stringify(sessionStorage).includes(s), SECRET), 'the value is in no markup, field or storage after Save');
    check(!sentRequests(srv).replace(/"value":"[^"]*"/g, '').includes(SECRET), 'the value appears in no logged request except the one Vault write');

    // Back: the saved name is selected, nothing is written again
    await p.click('[data-cfw-back]'); await step(p, 'permissions'); await p.click('[data-cfw-back]'); await step(p, 'setup');
    await p.waitForSelector('[data-pk-cred-vault="0"]');
    check(await p.inputValue('[data-pk-cred-vault="0"]') === 'youtube_api_key', 'coming back shows the saved credential selected');
    srv.secretWrites.length = 0; await primary(p); await step(p, 'permissions');
    check(srv.secretWrites.length === 0, 'Continue again writes nothing');
    check(pageErrors.length === 0, 'no page errors', pageErrors.join(' | '));
    await ctx.close();

    // 4b: pick an existing credential; a manual row keeps its editable name; an existing name is never replaced
    const s2 = makeServer(); s2.secrets.push({ name: 'youtube_api_key', scope: 'global', entry_type: 'api_key', allow_unattended: true });
    const two = await newPage(browser, { srv: s2, width, height: width === 390 ? 844 : 1000 }); const q = two.page;
    await toDetails(q);
    await q.selectOption('[data-pk-cred-vault="0"]', 'existing.key');
    await q.click('[data-pk-cred-add]');
    await q.waitForSelector('[data-pk-cred-env="1"]');
    const manual = await boxes(q, '[data-pk-cred="1"] input, [data-pk-cred="1"] select');
    check(manual.length === 2 && manual.every((f) => f.h <= 56), 'a row the person adds has an editable name and normal-height fields', JSON.stringify(manual));
    await q.fill('[data-pk-cred-env="1"]', 'EXTRA_TOKEN'); await q.click('[data-pk-cred-new="1"]'); await q.fill('[data-pk-cred-secret]', SECRET);
    await primary(q); await passcode(q, PASSCODE); await step(q, 'permissions');
    check(s2.secretWrites.length === 1 && s2.secretWrites[0].name === 'extra_token', 'the manual row is named from its own variable', JSON.stringify(s2.secretWrites.map((x) => x.name)));
    await q.click('[data-cfw-back]'); await step(q, 'setup');
    await q.click('[data-pk-cred-remove="1"]');
    await q.click('[data-pk-cred-new="0"]'); await q.fill('[data-pk-cred-secret]', SECRET);
    s2.secretWrites.length = 0; await primary(q); await passcode(q, PASSCODE); await step(q, 'permissions');
    check(s2.secretWrites.length === 1 && s2.secretWrites[0].name === 'youtube_api_key-2', 'a name already in the Vault is skipped, never replaced', JSON.stringify(s2.secretWrites.map((x) => x.name)));
    check(two.pageErrors.length === 0, 'no page errors (second run)', two.pageErrors.join(' | '));
    await two.ctx.close();
  }
} finally { await browser.close(); }
if (bad) { console.error(`${bad} package-credential checks failed`); process.exit(1); }
console.log('All package-credential checks passed');
