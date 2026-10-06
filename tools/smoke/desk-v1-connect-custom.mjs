#!/usr/bin/env node
/**
 * Desk v1 — "Add your own MCP server" and its approval card (docs/DESK_SERVICE_PROFILES_SPEC.md sections
 * 6.1 and 6.2, slice U2a), the browser half, against a fake server, `desk_v1_live` ON. The server half is
 * pinned by tests/test_desk_connect_custom.py; the card here has the keys the server's `_card` builds.
 *
 *   project   the default reach is one project; Review shows the exact command, the pin, the reach and the
 *             "User supplied" label and writes nothing; Save is off until the card is approved; a wrong
 *             passcode keeps the card; the Save carries only {request_id, fingerprint, passcode}; a
 *             registered result says Registered, never Connected.
 *   edits     changing a field drops the card and its approval: a new Review is a new card.
 *   global    choosing Global is explicit and the card names every project; a re-approval shows what changed.
 *   pending   a server saved but not runnable here says so and never says Registered or Connected.
 *   drift     an approved server whose package files moved since the approval reads Changed, lists the paths
 *             (changed, added, removed, and how many more) and offers no way around the approval (MC-1054).
 *   deps      slice U2b: a package with dependencies lists every one with its exact version and sha512 before
 *             Save; its install scripts are listed with their exact bodies and are OFF; ticking one reviews
 *             again with `approve_scripts`, gives a new card (new fingerprint, approval cleared); a script
 *             whose body cannot be shown exactly has no checkbox.
 *   + phone   no horizontal scroll, the Save button reachable at 390px.
 *
 * Screenshots: docs/desk_v1/screens/connect_custom_{card,global,pending,drift,deps}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-custom.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createHash } from 'node:crypto';
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
const DIGEST = 'sha512-' + 'A'.repeat(86) + '==';

function makeServer({ commitState = 'registered', previous = null, connections = [], deps = false } = {}) {
  const fx = loadFixtures();
  const srv = { log: [], fx, n: 0, cards: new Map(), commitState, connections };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'linkedin')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
  const sha = (c) => 'sha512-' + c.repeat(86) + '==';
  srv.depsCard = (b) => {
    const ticked = new Set(b.approve_scripts || []);
    const scripts = [
      { id: 'node_modules/native-helper#postinstall', path: 'node_modules/native-helper', package: 'native-helper', version: '2.0.1', script: 'postinstall', body: 'node scripts/fetch-binary.js --from https://cdn.example.com/helper.bin', body_escaped: null, approvable: true, reason: null },
      { id: 'node_modules/sneaky#install', path: 'node_modules/sneaky', package: 'sneaky', version: '0.3.0', script: 'install', body: null, body_escaped: 'node setup.js\u202e', approvable: false, reason: 'contains control or hidden characters, so it cannot be shown exactly' },
    ].map((x) => ({ ...x, approved: ticked.has(x.id) }));
    return {
      install_steps: scripts.filter((x) => x.approved).map((x) => ({ path: x.path, package: x.package, version: x.version, script: x.script, body: x.body })),
      install_note: 'Clayrune resolved every dependency itself, to an exact version, from the package registry, and lists each with the sha512 of its archive on this card.',
      dependencies: [
        { name: 'native-helper', version: '2.0.1', integrity: sha('B'), path: 'node_modules/native-helper', size_bytes: 48000, unpacked_bytes: 190000, licence: 'MIT', deprecated: false, registry_stated_digest: sha('B') },
        { name: 'sneaky', version: '0.3.0', integrity: sha('C'), path: 'node_modules/sneaky', size_bytes: 3000, unpacked_bytes: 9000, licence: 'ISC', deprecated: true, registry_stated_digest: sha('C') },
        { name: '@scope/deep-dependency-with-a-long-name', version: '10.20.30', integrity: sha('D'), path: 'node_modules/native-helper/node_modules/@scope/deep-dependency-with-a-long-name', size_bytes: 1000, unpacked_bytes: 4000, licence: null, deprecated: false, registry_stated_digest: sha('D') },
      ],
      dependency_totals: { count: 3, download_bytes: 52000, unpacked_bytes: 203000, members: 40 },
      scripts, scripts_note: "An install script runs the system shell with that package's folder as the working directory, a fixed environment with no Clayrune secrets, and the file and network access of this account. It is not a sandbox. A script you do not tick is not run.",
      optional_not_installed: [{ package: 'native-helper@2.0.1', optional: ['fsevents'] }], native_build_not_run: [],
    };
  };
  srv.card = (b) => {
    const name = b.server_name || 'harmless-mcp';
    const ticked = (b.approve_scripts || []).length;
    const project = b.scope === 'project' ? fx.projects.find((p) => p.id === b.project_id) : null;
    const op = { scope: b.scope, name, args: b.args || [], creds: b.credentials || [], ticked: b.approve_scripts || [] };
    const fingerprint = 'sha256:' + createHash('sha256').update(JSON.stringify(op)).digest('hex');
    const changes = previous && previous.scope !== b.scope ? [{ field: 'reach', from: previous.scope, to: b.scope }] : [];
    const local = "Runs with this account's file and network permissions; no sandbox.";
    const extra = deps ? srv.depsCard(b) : {};
    return {
      schema: 'desk-custom-card/1', request_id: 'req-' + (++srv.n) + '-abcdefghijklmnop', fingerprint,
      origin: { code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' },
      title: 'harmless-mcp@1.2.3', server_name: name, protocol: 'stdio',
      command: { command: '/usr/bin/python3', args: ['/clayrune/tools/with-secret.py', ...(b.credentials || []).flatMap((c) => ['--env', `${c.env}=${c.vault}`]), '--', 'node', '/data/mcp/harmless-mcp/1.2.3/package/dist/index.js', ...(b.args || [])], runnable: true },
      install_steps: [], install_note: 'Clayrune downloads this one archive itself, checks its sha512 and unpacks it. No npm, npx or install script runs.',
      working_directory: 'The folder of the agent session that starts it.', first_start: 'Deferred: nothing is started now.',
      package: { ecosystem: 'npm', registry: 'registry.npmjs.org', source: 'https://registry.npmjs.org/harmless-mcp/-/harmless-mcp-1.2.3.tgz', version: '1.2.3', integrity: DIGEST, pinned: true, entry: 'dist/index.js', size_bytes: 20480, unpacked_bytes: 61440, licence: 'MIT', publisher: { name: 'someone', status: 'claimed' }, registry_stated_digest: DIGEST, install_scripts_not_run: [] },
      credentials: (b.credentials || []).map((c) => ({ env: c.env, vault: c.vault, placement: 'environment variable of the server process', recipient: 'the server process' })),
      arguments: b.args || [],
      reach: project ? { scope: 'project', project: { id: project.id, name: project.name }, who: `Agents working in the project "${project.name}" only.`, local_code: local, secrets_to: 'none' }
        : { scope: 'global', project: null, who: 'Agents in every project.', local_code: local, secrets_to: 'none' },
      scope_default: 'project', scope_options: ['project', 'global'], scope_chosen: b.scope,
      risks: (deps ? [{ code: 'dependencies_installed', label: '3 dependency packages are installed with it, each pinned to the digest shown. They are not reviewed by Clayrune and run with the same access.' }] : [])
        .concat(deps && ticked ? [{ code: 'install_scripts_approved', label: `${ticked} install script(s) you approved run now, at Save, with this account's file and network access. Clayrune does not sandbox them.` }] : [])
        .concat([{ code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' }, { code: 'runs_local_code', label: "Runs code on this computer. It has your account's file and network access: Clayrune does not sandbox it." }])
        .concat(b.scope === 'global' ? [{ code: 'global_reach', label: "Global: agents in EVERY project can use this server's tools." }] : [])
        .concat([{ code: 'digest_not_safety', label: 'The digest proves the files are the ones you approve here. It does not prove they are safe.' }]),
      limitations: commitState === 'pending_runtime' ? [{ code: 'wrapper_missing', message: 'This install has no secret wrapper. You can still save: the approval is kept as pending.' }] : [],
      changes, reask: changes.length > 0, replaces: changes.length ? 'sha256:' + '1'.repeat(64) : null, approved: false,
      ...extra,
    };
  };
  return srv;
}

async function newPage(browser, { srv, width, height }) {
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
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const u = new URL(body.input);
      return J({ url: `https://${u.hostname}`, host: u.hostname, path: '/', service: null, input_kind: 'url',
        options: [{ method: 'save_for_agents', support: 'available', title: 'Save for agents', evidence: 'Always available', guidance: 'Saves a note for agents.', selectable: true }] });
    }
    if (path === '/api/desk/connect/custom/review' && method === 'POST') {
      if (!body.package) return J({ error: 'enter the npm package to add', code: 'bad_package' }, 400);
      const card = srv.card(body);
      srv.cards.set(card.request_id, card);
      return J(card);
    }
    if (path === '/api/desk/connect/custom/connections' && method === 'POST') return J({ connections: srv.connections });
    if (path === '/api/desk/connect/custom/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const card = srv.cards.get(body.request_id);
      if (!card || card.fingerprint !== body.fingerprint) return J({ error: 'the configuration changed since you reviewed it. Review it again.', code: 'changed_since_review' }, 409);
      const registered = srv.commitState === 'registered';
      return J({ ok: true, approved: true, duplicate: false, fingerprint: card.fingerprint, server_name: card.server_name, scope: card.scope_chosen, state: srv.commitState,
        code: registered ? '' : 'wrapper_missing', message: registered ? 'Registered. It starts the first time an agent session uses it.' : 'Saved. This install has no secret wrapper, so nothing is registered yet.' }, 201);
    }
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

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const commits = (srv) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/desk/connect/custom/commit');
const reviews = (srv) => srv.log.filter((r) => r.method === 'POST' && r.path === '/api/desk/connect/custom/review');
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|custom\/(review|connections))$/.test(r.path));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_custom_${name}_${w}.png`) });

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    const sv = document.querySelector('[data-cu-save]');
    if (sv) sv.scrollIntoView({ block: 'center' });
    const r = sv ? sv.getBoundingClientRect() : null;
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
      save: r ? { left: r.left, right: r.right } : null, vw: window.innerWidth };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  if (m.save) check(m.save.left >= 0 && m.save.right <= m.vw, `${label}: Save is inside the ${m.vw}px window`, `${label}: Save outside the window ${JSON.stringify(m.save)}`);
}

async function openCustom(page) {
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  await page.fill('[data-cf-url]', 'https://tools.example.com');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.waitForSelector('[data-cu="closed"]', { timeout: 4000 });
  await page.click('[data-cu-open]');
  await page.waitForSelector('[data-cu="open"] [data-cu-package]');
}
const passcodePrompt = async (page, code) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', code);
  await page.click('.modal-content .btn-add');
};

async function projectScenario(browser, width, height) {
  console.log(`Project reach at ${width}px: Review, approve, Save`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  check(await page.isChecked('[data-cu-scope-opt][value="project"]') && !(await page.isChecked('[data-cu-scope-opt][value="global"]')), 'the reach defaults to one project; global is not chosen', 'default scope is not project');
  check(await page.$eval('[data-cu-review]', (b) => b.disabled), 'Review is off until a package is named', 'Review enabled with no package');
  await page.fill('[data-cu-package]', 'harmless-mcp@1.2.3');
  await page.fill('[data-cu-args]', '--mode\nread-only');
  await page.click('[data-cu-cred-add]');
  await page.fill('[data-cu-cred-env="0"]', 'HARMLESS_TOKEN');
  await page.fill('[data-cu-cred-vault="0"]', 'harmless.token');
  const projectId = await page.$eval('[data-cu-project]', (s) => s.value);
  check(!!projectId, `a project is chosen on the form (${projectId})`, 'no project chosen');
  check(writes(srv).length === 0 && reviews(srv).length === 0, 'nothing is sent before Review', 'requests before Review');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-card]', { timeout: 4000 });
  const sent = reviews(srv)[0].body;
  check(sent.package === 'harmless-mcp@1.2.3' && sent.scope === 'project' && sent.project_id === projectId
    && sent.args.join() === '--mode,read-only' && sent.credentials.length === 1 && sent.credentials[0].vault === 'harmless.token',
    'Review sends the typed package, the project reach, the arguments and the credential NAMES', 'review body: ' + JSON.stringify(sent));
  check(!JSON.stringify(sent).includes('"value"') && !('passcode' in sent), 'the Review carries no credential value and no passcode', 'a value or passcode in the Review');
  const argv = await page.$$eval('[data-cu-command] li', (e) => e.map((x) => x.textContent));
  check(argv[0] === '/usr/bin/python3' && argv.includes('node') && argv.includes('/data/mcp/harmless-mcp/1.2.3/package/dist/index.js') && argv.slice(-2).join() === '--mode,read-only',
    'the card shows the exact command, one argument per line', 'argv: ' + JSON.stringify(argv));
  const pin = await page.textContent('[data-cu-pin]');
  check(/1\.2\.3/.test(pin) && pin.includes(DIGEST) && /never updates/.test(pin), 'the card shows the version pin and the digest', 'pin line: ' + pin);
  const reach = await page.$eval('[data-cu-reach]', (e) => [e.dataset.cuReach, e.textContent]);
  check(reach[0] === 'project' && /only/.test(reach[1]) && /no sandbox/.test(reach[1]), 'the card names the reach: one project, local code with no sandbox', 'reach: ' + reach);
  check((await page.textContent('[data-cu-origin]')).includes('User supplied; not reviewed by Clayrune'), 'the card is labelled User supplied; not reviewed by Clayrune', 'origin label missing');
  const risks = await page.$$eval('[data-cu-risk]', (e) => e.map((x) => x.dataset.cuRisk));
  check(risks.includes('runs_local_code') && risks.includes('digest_not_safety') && !risks.includes('global_reach'), `risk labels are shown (${risks.join()})`, 'risks: ' + risks);
  check((await page.textContent('[data-cu-credentials]')).includes('harmless.token'), 'a credential shows as a Secrets entry name and an environment variable', 'credential line wrong');
  check(await page.$eval('[data-cu-save]', (b) => b.disabled), 'Save is off until the card is approved', 'Save enabled before approval');
  check(writes(srv).length === 0, 'nothing has been written (only inspect and review were called)', 'writes so far: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await page.check('[data-cu-approve]');
  check(!(await page.$eval('[data-cu-save]', (b) => b.disabled)), 'ticking the approval turns Save on', 'Save still off');
  await shot(page, 'card', width);
  await fits(page, 'approval card');

  await page.click('[data-cu-save]');
  await passcodePrompt(page, 'wrong-passcode');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  await page.click('.modal-content .btn-secondary');
  check(commits(srv).length === 1 && (await page.$$('[data-cu-card]')).length === 1, 'a wrong passcode reaches the server once and the card stays', 'commits ' + commits(srv).length);
  await page.click('[data-cu-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cu-result="registered"]', { timeout: 6000 });
  const body = commits(srv)[1].body;
  check(Object.keys(body).sort().join() === 'fingerprint,passcode,request_id', 'the Save carries only the request id, the fingerprint and the passcode: no command, version or scope', 'commit body keys: ' + Object.keys(body));
  const text = await page.textContent('[data-cu-result]');
  check(/Registered/.test(text) && !/Connected/.test(text) && /starts the first time/.test(text), 'the result says Registered and when it starts, never Connected', 'result text: ' + text);
  check((await page.$$('[data-cu-card]')).length === 0, 'the card is gone once saved', 'the card stayed');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function editScenario(browser, width, height) {
  console.log(`Edits at ${width}px: a changed form is a new Review`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  await page.fill('[data-cu-package]', 'harmless-mcp');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-card]');
  await page.check('[data-cu-approve]');
  const first = await page.getAttribute('[data-cu-card]', 'data-cu-card');
  await page.fill('[data-cu-args]', '--different');
  check((await page.$$('[data-cu-card]')).length === 0, 'editing an argument drops the card and its approval', 'the card survived an edit');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-card]');
  check(!(await page.isChecked('[data-cu-approve]')) && (await page.getAttribute('[data-cu-card]', 'data-cu-card')) !== first, 'the new card has a new fingerprint and starts unapproved', 'approval carried over');
  check(reviews(srv).length === 2 && commits(srv).length === 0, 'two Reviews, no Save', 'reviews ' + reviews(srv).length);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function globalScenario(browser, width, height) {
  console.log(`Global reach at ${width}px: explicit, named, re-asked`);
  const srv = makeServer({ previous: { scope: 'project' } });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  await page.fill('[data-cu-package]', 'harmless-mcp');
  await page.check('[data-cu-scope-opt][value="global"]');
  check(await page.$eval('[data-cu-project]', (s) => s.disabled), 'choosing Global turns the project picker off', 'project picker still on');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-card]');
  check(reviews(srv)[0].body.scope === 'global' && !('project_id' in reviews(srv)[0].body), 'the Review asks for global with no project', 'review body: ' + JSON.stringify(reviews(srv)[0].body));
  const reach = await page.$eval('[data-cu-reach]', (e) => [e.dataset.cuReach, e.textContent]);
  check(reach[0] === 'global' && /every project/.test(reach[1]), 'the card says agents in every project can use it', 'reach: ' + reach);
  check((await page.$$('[data-cu-risk="global_reach"]')).length === 1, 'a global-reach risk label is shown', 'no global risk label');
  const reask = await page.$eval('[data-cu-reask]', (e) => e.textContent);
  check(/new approval/.test(reask) && (await page.$$('[data-cu-change="reach"]')).length === 1, 'a server approved before at another reach shows what changed and asks again', 'reask: ' + reask);
  await page.check('[data-cu-approve]');
  await shot(page, 'global', width);
  await fits(page, 'global card');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function pendingScenario(browser, width, height) {
  console.log(`Pending at ${width}px: a server that cannot run here is not called registered`);
  const srv = makeServer({ commitState: 'pending_runtime' });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  await page.fill('[data-cu-package]', 'harmless-mcp');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-card]');
  check((await page.$$('[data-cu-limit="wrapper_missing"]')).length === 1, 'the card states what this install cannot do, before Save', 'limitation missing');
  await page.check('[data-cu-approve]');
  await page.click('[data-cu-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cu-result="pending_runtime"]', { timeout: 6000 });
  const text = await page.textContent('[data-cu-result]');
  check(/Saved, cannot run yet/.test(text) && !/Connected/.test(text) && !/Registered/.test(text.replace('nothing is registered', '')), 'the result says saved but cannot run yet, never Registered or Connected', 'result text: ' + text);
  await shot(page, 'pending', width);
  await fits(page, 'pending result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const DRIFT = { status: 'changed', reason: 'files_differ', counts: { changed: 1, added: 11, removed: 1 }, changed: ['package/dist/index.js'],
  added: Array.from({ length: 10 }, (_, i) => `package/dist/planted${i}.js`), removed: ['package/package.json'], more: 1, checked: 40 };
const NPM = { ecosystem: 'npm', scope: 'project', project_id: 'p1', package: 'harmless-mcp', version: '1.2.3', credentials: [] };

async function driftScenario(browser, width, height) {
  console.log(`Drift at ${width}px: package files that moved since the approval`);
  const srv = makeServer({ connections: [
    { ...NPM, server_name: 'harmless-mcp', state: 'changed', code: 'package_files_changed', package_files: DRIFT,
      message: 'The package files on disk no longer match what was approved. Connect it again and approve the change; saving records the files as they are now.' },
    { ...NPM, server_name: 'steady-mcp', state: 'registered', message: 'Registered. It starts the first time an agent session uses it.', package_files: { status: 'unchanged', counts: { changed: 0, added: 0, removed: 0 }, changed: [], added: [], removed: [], more: 0 } },
    { ...NPM, server_name: 'older-mcp', state: 'registered', message: 'Registered. It starts the first time an agent session uses it.', package_files: { status: 'not_recorded', counts: { changed: 0, added: 0, removed: 0 }, changed: [], added: [], removed: [], more: 0 } },
    { ecosystem: 'remote', server_name: 'remote-mcp', state: 'registered', message: 'Registered.', credentials: [] },
  ] });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  await page.waitForSelector('[data-cu-approved]', { timeout: 4000 });
  const rows = await page.$$eval('[data-cu-approved-row]', (e) => e.map((x) => [x.dataset.cuApprovedRow, x.dataset.cuApprovedState]));
  check(rows.length === 3 && !rows.some((r) => r[0] === 'remote-mcp'), 'the list shows the three npm servers and not the remote one', 'rows: ' + JSON.stringify(rows));
  const bad1 = await page.$eval('[data-cu-approved-row="harmless-mcp"]', (e) => ({ text: e.textContent, kind: e.dataset.cfMsg }));
  check(bad1.kind === 'warn' && /Changed/.test(bad1.text) && /no longer match what was approved/.test(bad1.text), 'the changed server reads Changed, in the same wording as a changed launch line', 'changed row: ' + bad1.text);
  const paths = await page.$$eval('[data-cu-approved-row="harmless-mcp"] [data-cu-drift-path]', (e) => e.map((x) => x.dataset.cuDriftPath + ':' + x.querySelector('code').textContent));
  check(paths.length === 12 && paths[0] === 'changed:package/dist/index.js' && paths.includes('removed:package/package.json') && paths.filter((p) => p.startsWith('added:')).length === 10, 'it lists the changed, added and removed paths', 'paths: ' + JSON.stringify(paths));
  check(/and 1 more/.test(await page.textContent('[data-cu-approved-row="harmless-mcp"] [data-cu-drift-more]')), 'it says how many paths are not listed', 'no "more" line');
  check((await page.$$('[data-cu-approved-row="steady-mcp"] [data-cu-drift]')).length === 0 && /Registered/.test(await page.textContent('[data-cu-approved-row="steady-mcp"]')), 'an unchanged server shows no file list', 'steady row has drift');
  check((await page.$$('[data-cu-approved-row="older-mcp"] [data-cu-files-note="not_recorded"]')).length === 1, 'an approval from before the check says its files are not checked', 'no not_recorded note');
  check(writes(srv).length === 0 && commits(srv).length === 0, 'looking writes nothing and saves nothing', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await shot(page, 'drift', width);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function depsScenario(browser, width, height) {
  console.log(`Dependencies at ${width}px: the closure and the install scripts are on the card, scripts off`);
  const srv = makeServer({ deps: true });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await openCustom(page);
  await page.fill('[data-cu-package]', 'harmless-mcp');
  await page.click('[data-cu-review]');
  await page.waitForSelector('[data-cu-dependencies]', { timeout: 4000 });
  const deps = await page.$$eval('[data-cu-dep]', (e) => e.map((x) => [x.dataset.cuDep, x.querySelector('strong').textContent, x.querySelector('code').textContent, x.querySelector('[data-cu-dep-digest]').textContent]));
  check(deps.length === 3 && deps[0][1] === 'native-helper' && deps[0][2] === '2.0.1' && /^sha512-B{86}==$/.test(deps[0][3]), 'every dependency is listed with its exact name, version and sha512, before Save', 'deps: ' + JSON.stringify(deps));
  check(deps.some((d) => d[0].includes('@scope/deep-dependency-with-a-long-name')), 'a nested package shows the place it is installed', 'nested path missing');
  check((await page.textContent('[data-cu-deps] summary')).includes('3 dependency packages'), 'the card says how many dependency packages come with it', 'no count');
  check((await page.$$('[data-cu-dep-deprecated]')).length === 1, 'a deprecated dependency is flagged', 'deprecated flag missing');
  check((await page.$$('[data-cu-risk="dependencies_installed"]')).length === 1, 'the dependency risk label is shown', 'no dependency risk');
  check((await page.$$('[data-cu-not-installed] [data-cu-optional-skipped]')).length === 1, 'optional packages that are not installed are listed', 'optional list missing');
  const ticks = await page.$$eval('[data-cu-script-tick]', (e) => e.map((x) => [x.dataset.cuScriptTick, x.checked]));
  check(ticks.length === 1 && ticks[0][1] === false, 'an install script has a checkbox and it is OFF', 'ticks: ' + JSON.stringify(ticks));
  const bodies = await page.$$eval('[data-cu-script-body]', (e) => e.map((x) => x.textContent));
  check(bodies[0] === 'node scripts/fetch-binary.js --from https://cdn.example.com/helper.bin', 'the exact script body is shown', 'body: ' + bodies[0]);
  check((await page.$$('[data-cu-script-approvable="false"] [data-cu-script-tick]')).length === 0 && /cannot be approved/.test(await page.textContent('[data-cu-script-approvable="false"]')), 'a script whose body cannot be shown exactly has no checkbox and says why', 'non-approvable script is tickable');
  check(reviews(srv).length === 1 && !('approve_scripts' in reviews(srv)[0].body), 'the first Review approves no script', 'first review: ' + JSON.stringify(reviews(srv)[0].body));
  await page.check('[data-cu-approve]');
  const first = await page.getAttribute('[data-cu-card]', 'data-cu-card');
  await page.$eval('[data-cu-dependencies]', (e) => e.scrollIntoView({ block: 'start' }));
  await shot(page, 'deps', width);
  await fits(page, 'dependency card');

  await page.check('[data-cu-script-tick]');
  await page.waitForFunction(() => document.querySelector('[data-cu-script-tick]') && document.querySelector('[data-cu-script-tick]').checked && document.querySelector('[data-cu-risk="install_scripts_approved"]'), null, { timeout: 4000 });
  check(reviews(srv).length === 2 && reviews(srv)[1].body.approve_scripts.join() === 'node_modules/native-helper#postinstall', 'ticking a script reviews again with that script id', 'second review: ' + JSON.stringify(reviews(srv)[1] && reviews(srv)[1].body));
  check((await page.getAttribute('[data-cu-card]', 'data-cu-card')) !== first && !(await page.isChecked('[data-cu-approve]')) && await page.$eval('[data-cu-save]', (b) => b.disabled), 'it is a new card: a new fingerprint, the approval cleared, Save off', 'approval carried over');
  check(/Runs once at Save/.test(await page.textContent('[data-cu-script="node_modules/native-helper#postinstall"]')), 'the card says the ticked script runs once at Save', 'ticked wording missing');
  check(commits(srv).length === 0 && writes(srv).length === 0, 'nothing is saved or written by ticking', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await page.check('[data-cu-approve]');
  await page.click('[data-cu-save]');
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-cu-result="registered"]', { timeout: 6000 });
  check(Object.keys(commits(srv)[0].body).sort().join() === 'fingerprint,passcode,request_id', 'the Save still carries only the request id, the fingerprint and the passcode: no script, no dependency', 'commit body: ' + Object.keys(commits(srv)[0].body));

  const again = await newPage(browser, { srv: makeServer({ deps: true }), width, height });
  await openCustom(again.page);
  await again.page.fill('[data-cu-package]', 'harmless-mcp');
  await again.page.click('[data-cu-review]');
  await again.page.waitForSelector('[data-cu-script-tick]');
  await again.page.check('[data-cu-script-tick]');
  await again.page.waitForSelector('[data-cu-script-tick]:checked');
  await again.page.fill('[data-cu-args]', '--other');
  check((await again.page.$$('[data-cu-card]')).length === 0, 'editing the form drops the card and its ticked scripts', 'card survived an edit');
  await again.page.click('[data-cu-review]');
  await again.page.waitForSelector('[data-cu-script-tick]');
  check(!(await again.page.isChecked('[data-cu-script-tick]')), 'a fresh Review starts with every script off again', 'a tick survived a new Review');
  await again.ctx.close();
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await projectScenario(browser, w, h);
    await editScenario(browser, w, h);
    await globalScenario(browser, w, h);
    await pendingScenario(browser, w, h);
    await driftScenario(browser, w, h);
    await depsScenario(browser, w, h);
  }
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
