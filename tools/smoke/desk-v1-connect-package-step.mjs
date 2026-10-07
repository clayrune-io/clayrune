#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's PACKAGE MCP screens (MC-1062 ticket 10,
 * docs/desk_v1/connect_flow_tickets/10-package-screen.md; static/js/desk-v1-connect-package-step.js), the browser
 * half, against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection screens and the
 * Permissions screen are the real ones. The server half (exact fingerprint, passcode, unattended refusal,
 * checksum, the Node search-path launch gate) stays pinned by tests/test_desk_connect_custom*.py and
 * tests/test_desk_connect_npm_launch_gate.py; the card here has the keys `custom_npm_card` builds.
 *
 *   1. Order        package first, detected settings second, Permissions third, facts and scripts last; the wizard
 *                   sends nothing but the read-only detection until Save.
 *   2. Detect       npm detection fills only empty fields, never npx arguments; credentials arrive as NAMES.
 *   3. Review       exact command, pin, source, reach, credential refs and the unreviewed-code notice stay on the
 *                   first review page; publisher, licence, size and every dependency are under ONE Details, 4 a page.
 *   4. Scripts      at most four per page, every full body visible, each OFF; a body that cannot be shown exactly has
 *                   no checkbox; ticking one reviews again (new fingerprint, approval cleared, `approve_scripts`).
 *   5. Invalidate   a changed argument, reach or script tick is a new review; an old approval never saves it.
 *   6. Save         off until approved; only {request_id, fingerprint, passcode}; a cancelled prompt sends nothing;
 *                   a stale card (changed_since_review) is dropped with the server's words.
 *   7. Result       Registered / Saved, cannot run yet / Saved, setup failed: the server's words, never "Connected".
 *   8. PyPI         read for suggestions only: no npm review, no pretending anything was installed.
 *   9. Fit          no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: docs/desk_v1/screens/connect_package_*_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-package-step.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
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
const STATIC = loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true });

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));
const PASSCODE = 'right-passcode';
const DIGEST = 'sha512-' + 'A'.repeat(86) + '==';
const sha = (c) => 'sha512-' + c.repeat(86) + '==';

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
got = resolve.resolve('x.com', own_hosts=('mc.smoke.test',))
svc = got.pop('service') or registry.lookup(got['host'])
print(json.dumps({'x.com': {**got, **type_view.project_service(svc['id'] if svc else None)}}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

// The shape of mc.desk_connect.parameter_detect.detect() for an npm package: one alternative that runs the package's own
// start command, one that is the README's `npx -y pkg` (its arguments are for npx and must never be suggested here).
const NPM_ALT = (id, command, args, credentials) => ({
  id, label: 'Detected from untrusted evidence', editable: true, approved: false, incomplete: false, transport: 'stdio', credentials,
  risk_flags: ['detected_from_untrusted_evidence', 'install_scripts'],
  package: { ecosystem: 'npm', name: 'harmless-mcp', resolved_version: '1.2.3', bin: ['harmless-mcp'], install_scripts: ['postinstall'] },
  fields: { command: { value: command }, args: { value: args } },
});
const DETECT_NPM = {
  ok: true, kind: 'npm', status: 'incomplete', warning: 'Detected from untrusted text. Nothing here is approved, installed or connected.', approved: false, problems: [],
  evidence: [{ id: 'e1', kind: 'npm_registry', label: 'npm registry: harmless-mcp', source: 'registry.npmjs.org', chars: 0 }, { id: 'e2', kind: 'readme', label: 'npm README: harmless-mcp', source: 'e1', chars: 331, read_by_isolated_reader: true }],
  alternatives: [NPM_ALT('a1', 'harmless-mcp', ['--mode', 'read-only'], []),
    NPM_ALT('a2', 'npx', ['-y', 'harmless-mcp@1.2.3'], [{ name: 'HARMLESS_TOKEN', placement: 'env' }, { name: 'Authorization', placement: 'header' }])],
};
const DETECT_PYPI = {
  ok: true, kind: 'pypi', status: 'incomplete', warning: 'Detected from untrusted text.', approved: false, problems: [], evidence: [{ id: 'e1', kind: 'pypi_registry', label: 'PyPI: acme-mcp', source: 'pypi.org', chars: 0 }],
  alternatives: [{ id: 'a1', transport: 'stdio', credentials: [], risk_flags: ['source_build_required'], package: { ecosystem: 'pypi', name: 'acme-mcp', resolved_version: '0.4.0', bin: [] }, fields: {} }],
};

function makeServer({ commitState = 'registered', commitCode = '' } = {}) {
  const fx = loadFixtures();
  const srv = { log: [], fx, n: 0, cards: new Map(), commitState, commitCode, expire: false };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  const SCRIPTS = [1, 2, 3, 4, 5, 6].map((i) => ({ id: `node_modules/dep-${i}#postinstall`, path: `node_modules/dep-${i}`, package: `dep-${i}`, version: `${i}.0.0`, script: 'postinstall', body: `node scripts/fetch-${i}.js --from https://cdn.example.com/${i}.bin\necho "second line of script ${i}"`, body_escaped: null, approvable: true, reason: null }))
    .concat([{ id: 'node_modules/sneaky#install', path: 'node_modules/sneaky', package: 'sneaky', version: '0.3.0', script: 'install', body: null, body_escaped: 'node setup.js‮', approvable: false, reason: 'contains control or hidden characters, so it cannot be shown exactly' }]);
  const DEPS = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => ({ name: `dep-${i}`, version: `${i}.0.0`, integrity: sha('BCDEFGHIJ'[i - 1]), path: `node_modules/dep-${i}`, size_bytes: 48000, unpacked_bytes: 190000, licence: 'MIT', deprecated: i === 2, registry_stated_digest: sha('B') }));
  srv.card = (b) => {
    const name = b.server_name || 'harmless-mcp';
    const ticked = new Set(b.approve_scripts || []);
    const scripts = SCRIPTS.map((x) => ({ ...x, approved: ticked.has(x.id) }));
    const project = b.scope === 'project' ? fx.projects.find((p) => p.id === b.project_id) : null;
    const fingerprint = 'sha256:' + createHash('sha256').update(JSON.stringify({ n: name, s: b.scope, p: b.project_id, a: b.args || [], c: b.credentials || [], e: b.entry || '', t: [...ticked].sort() })).digest('hex');
    const local = "Runs with this account's file and network permissions; no sandbox.";
    return {
      schema: 'desk-custom-card/1', request_id: 'req-' + (++srv.n) + '-abcdefghijklmnop', fingerprint,
      origin: { code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' },
      title: 'harmless-mcp@1.2.3', server_name: name, protocol: 'stdio',
      command: { command: '/usr/bin/python3', args: ['/clayrune/tools/with-secret.py', ...(b.credentials || []).flatMap((c) => ['--env', `${c.env}=${c.vault}`]), '--', 'node', '/data/mcp/harmless-mcp/1.2.3/package/dist/index.js', ...(b.args || [])], runnable: true },
      install_steps: scripts.filter((x) => x.approved).map((x) => ({ path: x.path, package: x.package, version: x.version, script: x.script, body: x.body })),
      install_note: 'Clayrune resolved every dependency itself, to an exact version, from the package registry, and lists each with the sha512 of its archive. No npm, npx or install script runs unless you tick it.',
      working_directory: 'The folder of the agent session that starts it.', first_start: 'Deferred: nothing is started now.',
      package: { ecosystem: 'npm', registry: 'registry.npmjs.org', source: 'https://registry.npmjs.org/harmless-mcp/-/harmless-mcp-1.2.3.tgz', version: '1.2.3', integrity: DIGEST, pinned: true, entry: 'dist/index.js', size_bytes: 20480, unpacked_bytes: 61440, licence: 'MIT', publisher: { name: 'acme', status: 'claimed by the registry' } },
      credentials: (b.credentials || []).map((c) => ({ env: c.env, vault: c.vault, placement: 'environment variable of the server process', recipient: 'the server process' })),
      arguments: b.args || [],
      reach: project ? { scope: 'project', project: { id: project.id, name: project.name }, who: `Agents working in the project "${project.name}" only.`, local_code: local, secrets_to: 'none' }
        : { scope: 'global', project: null, who: 'Agents in every project.', local_code: local, secrets_to: 'none' },
      scope_chosen: b.scope,
      risks: [{ code: 'dependencies_installed', label: '9 dependency packages are installed with it, each pinned to the digest shown. They are not reviewed by Clayrune and run with the same access.' },
        { code: 'user_supplied', label: 'User supplied; not reviewed by Clayrune' }, { code: 'runs_local_code', label: "Runs code on this computer. It has your account's file and network access: Clayrune does not sandbox it." },
        { code: 'digest_not_safety', label: 'The digest proves the files are the ones you approve here. It does not prove they are safe.' }]
        .concat(b.scope === 'global' ? [{ code: 'global_reach', label: "Global: agents in EVERY project can use this server's tools." }] : []),
      limitations: [], changes: [], reask: false, replaces: null, approved: false,
      dependencies: DEPS, dependency_totals: { count: 9, download_bytes: 432000, unpacked_bytes: 1710000, members: 90 },
      scripts, scripts_note: "An install script runs the system shell with that package's folder as the working directory, a fixed environment with no Clayrune secrets, and the file and network access of this account. It is not a sandbox. A script you do not tick is not run.",
      optional_not_installed: [{ package: 'dep-1@1.0.0', optional: ['fsevents'] }], native_build_not_run: [],
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
    if (path === '/static/js/desk-v1-connection-status.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its status words and MCP tiles would add a page-load read to a smoke that counts requests
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/static/js/desk-v1-connect-api-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its own Setup screen would shadow this smoke's fixture
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
    if (path.startsWith('/api/secrets') || path.startsWith('/api/browser')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/types' && method === 'POST') {
      const t = TYPES[String((body || {}).input || '').trim()];
      return t ? J(t) : J({ error: 'unknown', code: 'unknown_name' }, 400);
    }
    if (path === '/api/desk/connect/detect' && method === 'POST') {
      if (body.kind === 'pypi') return J(DETECT_PYPI);
      if (body.input === 'missing-pkg') return J({ error: 'That package was not found on npm.', code: 'not_found' }, 404);
      return J(DETECT_NPM);
    }
    if (path === '/api/desk/connect/custom/review' && method === 'POST') {
      if (!body.package) return J({ error: 'enter the npm package to add', code: 'bad_package' }, 400);
      const card = srv.card(body);
      srv.cards.set(card.request_id, card);
      return J(card);
    }
    if (path === '/api/desk/connect/custom/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const card = srv.cards.get(body.request_id);
      if (srv.expire || !card || card.fingerprint !== body.fingerprint) return J({ error: 'the configuration changed since you reviewed it. Review it again.', code: 'changed_since_review' }, 409);
      const st = srv.commitState;
      const msg = { registered: 'Registered. It starts the first time an agent session uses it.', pending_runtime: 'Saved. This install has no secret wrapper, so nothing is registered yet.', setup_failed: 'Saved, but setup failed: the package archive could not be unpacked.' }[st];
      return J({ ok: true, approved: true, duplicate: false, fingerprint: card.fingerprint, server_name: card.server_name, scope: card.scope_chosen, state: st, code: srv.commitCode, message: msg }, 201);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors, srv };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_package_${name}_${w}.png`) });
const reviews = (srv) => srv.log.filter((r) => r.path === '/api/desk/connect/custom/review');
const commits = (srv) => srv.log.filter((r) => r.path === '/api/desk/connect/custom/commit');
const detects = (srv) => srv.log.filter((r) => r.path === '/api/desk/connect/detect');
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(types|detect|custom\/review)$/.test(r.path));
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const has = (page, sel) => page.$(sel).then(Boolean);
const disabled = (page, sel) => page.$eval(sel, (b) => b.disabled);
const screenId = (page) => page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen);
const waitScreen = (page, id) => page.waitForFunction((i) => { const r = document.querySelector('[data-cfw]'); return !!r && r.dataset.cfwScreen === i; }, id, { timeout: 4000 });
const waitTitle = (page, t) => page.waitForFunction((x) => { const e = document.querySelector('[data-cfw-title]'); return !!e && e.textContent === x; }, t, { timeout: 4000 });
const waitStep = (page, s) => page.waitForSelector(`[data-cfw][data-cfw-step="${s}"]`, { timeout: 4000 });
const passcodePrompt = async (page, code) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', code);
  await page.click('.modal-content .btn-add');
};
const clickPrimary = (page) => page.click('[data-cfw-primary]');
const openDetails = async (page) => { if (!(await page.$eval('[data-cfw-details]', (d) => d.open))) await page.click('[data-cfw-details] > summary'); };

async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    return { words, alts: root.querySelectorAll('[data-cfw-option], [data-cfw-alt]').length, details: root.querySelectorAll('details').length,
      nested: root.querySelectorAll('details details, form form').length, forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length };
  });
  check(m.words <= 25, `${label}: ${m.words} explanatory words (<= 25)`, `${label}: ${m.words} explanatory words`);
  check(m.alts <= 4, `${label}: ${m.alts} alternatives (<= 4)`, `${label}: ${m.alts} alternatives`);
  check(m.details <= 1 && m.nested === 0, `${label}: at most one Details, nothing nested (${m.details})`, `${label}: details ${m.details}, nested ${m.nested}`);
  check(m.forms === 1 && m.primaries <= 1, `${label}: one form, ${m.primaries} primary action`, `${label}: forms ${m.forms}, primaries ${m.primaries}`);
}

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const scroller = document.querySelector('[data-connections]');
    const out = { docOver: document.documentElement.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0, vw: window.innerWidth, vh: window.innerHeight, controls: [] };
    for (const sel of ['[data-cfw-primary]', '[data-cfw-back]']) {
      const b = document.querySelector(sel);
      if (!b) continue;
      b.scrollIntoView({ block: 'nearest' });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out.controls.push({ sel, left: r.left, right: r.right, top: r.top, bottom: r.bottom, reachable: !!hit && (hit === b || b.contains(hit)) });
    }
    return out;
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  for (const c of m.controls) check(c.reachable && c.left >= 0 && c.right <= m.vw + 0.5 && c.top >= 0 && c.bottom <= m.vh + 1, `${label}: ${c.sel} is inside the ${m.vw}x${m.vh} window and not covered`, `${label}: ${JSON.stringify(c)}`);
}

// Open the wizard on x.com, take the API way to reach a fixture Setup screen, and switch the branch to the npm package MCP.
// (The Package/Server-address choice that sets this variant is not this ticket's: the smoke sets it through the frame's own `select`.)
async function toPackageSetup(page) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await page.evaluate(() => {
    window.DeskV1ConnectWizard.registerScreen({ id: 'fx-setup', step: 'setup', match: (sel) => sel.type === 'api', title: () => 'Fixture setup', copy: () => 'x', body: () => '<p>Setup</p>', bind: (root, api) => { window.__fxApi = api; }, primary: () => ({ label: 'Continue' }) });
  });
  await page.fill('[data-cfw-input]', 'x.com');
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check('[data-cfw-option="api"] input');
  await clickPrimary(page);
  await waitScreen(page, 'fx-setup');
  await page.evaluate(() => { window.__fxApi.select({ type: 'mcp', variant: 'custom-npm' }); window.__fxApi.go('setup'); });
  await waitScreen(page, 'package-setup');
}

// Setup (type, Detect, name the credential), Permissions (choose the project), then the first Review page.
async function toReview(page, { pkg = 'harmless-mcp@1.2.3', detect = true, vault = 'harmless.token' } = {}) {
  await toPackageSetup(page);
  await page.fill('[data-pk-package]', pkg);
  if (detect) { await page.click('[data-pk-detect]'); await page.waitForSelector('[data-pk-detect-state="incomplete"]'); }
  await clickPrimary(page);
  await waitTitle(page, 'Package details');
  if (vault && (await has(page, '[data-pk-cred-vault="0"]'))) await page.fill('[data-pk-cred-vault="0"]', vault);
  await clickPrimary(page);
  await waitStep(page, 'permissions');
  const id = await page.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value);
  await page.selectOption('[data-cfp-project]', id);
  await clickPrimary(page);
  await waitScreen(page, 'package-review');
  await page.waitForSelector('[data-pk-command]', { timeout: 4000 });
  return id;
}
async function toApprove(page) {
  await clickPrimary(page); await waitTitle(page, 'Install steps');
  await clickPrimary(page); await waitTitle(page, 'Approve connection');
}

async function mainScenario(browser, width, height) {
  console.log(`\n== ${width}px: package, detect, permissions, review, scripts, save ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toPackageSetup(page);

  // 1: the first screen is the package and nothing else
  check((await text(page, '[data-cfw-title]')) === 'MCP package' && (await screenId(page)) === 'package-setup', 'Setup opens on "MCP package"', 'setup title: ' + await text(page, '[data-cfw-title]'));
  check(!(await has(page, '[data-pk-args], [data-pk-entry], [data-pk-name], [data-cfp-reach], [data-cu-script-tick]')), 'no settings, reach or script control on the first screen', 'a later control showed on the first screen');
  check(await disabled(page, '[data-cfw-primary]') && await disabled(page, '[data-pk-detect]'), 'Continue and Detect are off until a package is named', 'controls on with no package');
  check((await text(page, '[data-cfw-stepof]')) === 'Setup · 1 of 2', 'the step counter reads "Setup · 1 of 2"', await text(page, '[data-cfw-stepof]'));
  await rules(page, 'Package');
  await shot(page, 'identify', width);
  await fits(page, 'Package');

  // 2: detect
  await page.fill('[data-pk-package]', 'harmless-mcp@1.2.3');
  check(!(await disabled(page, '[data-cfw-primary]')), 'naming a package turns Continue on', 'Continue still off');
  await page.click('[data-pk-detect]');
  await page.waitForSelector('[data-pk-detect-state="incomplete"]');
  const dsent = detects(srv)[0].body;
  check(detects(srv).length === 1 && dsent.kind === 'npm' && dsent.input === 'harmless-mcp@1.2.3', 'Detect sends kind npm and the package, once', 'detect: ' + JSON.stringify(detects(srv).map((d) => d.body)));
  const dline = await text(page, '[data-pk-detect-slot]');
  check(/suggestions, not approved/.test(dline) && /incomplete/.test(dline), 'the detection line says the settings are suggestions, not approved, and that the answer is incomplete', dline);
  check(writes(srv).length === 0 && reviews(srv).length === 0, 'detection wrote nothing and started no review', 'requests: ' + JSON.stringify(srv.log.map((r) => r.path)));
  await clickPrimary(page);
  await waitTitle(page, 'Package details');
  check((await text(page, '[data-cfw-stepof]')) === 'Setup · 2 of 2', 'the second Setup page reads "Setup · 2 of 2"', await text(page, '[data-cfw-stepof]'));
  const args = await page.$eval('[data-pk-args]', (t) => t.value);
  check(args === '--mode\nread-only', 'arguments are suggested only from the package\'s own start command, never the README\'s npx arguments', 'args: ' + JSON.stringify(args));
  check((await page.$eval('[data-pk-cred-env="0"]', (i) => i.value)) === 'HARMLESS_TOKEN' && (await page.$$('[data-pk-cred]')).length === 1 && (await page.$eval('[data-pk-cred-vault="0"]', (i) => i.value)) === '',
    'one credential NAME is suggested (the environment variable, not the header), with no Vault entry chosen for the person', 'credential rows wrong');
  check(await has(page, '[data-cfw-details] [data-pk-name]') && !(await has(page, '[data-cfw-body] [data-pk-name]')), 'the optional server name sits under Details, not in the body', 'server name placement');
  await openDetails(page);
  const prov = await text(page, '[data-pk-provenance]');
  check(/untrusted text/i.test(prov) && /Evidence: npm README/.test(prov), 'Details says where the suggestions came from', prov);
  await rules(page, 'Package details');
  await shot(page, 'details', width);
  await fits(page, 'Package details');
  await page.fill('[data-pk-cred-vault="0"]', 'harmless.token');
  await page.fill('[data-pk-name]', 'harmless-notes');

  // Permissions is the real screen, third
  await clickPrimary(page);
  await waitStep(page, 'permissions');
  check((await screenId(page)) === 'permissions-step' && (await has(page, '[data-cfp-reach]')) && !(await has(page, '[data-cfp-box]')), 'Permissions comes after the settings and shows the reach only', 'permissions screen: ' + await screenId(page));
  check(reviews(srv).length === 0 && writes(srv).length === 0, 'nothing has been reviewed or written before the Review step', 'requests: ' + JSON.stringify(srv.log.map((r) => r.path)));
  const projectId = await page.$eval('[data-cfp-project] option:not([value=""])', (o) => o.value);
  await page.selectOption('[data-cfp-project]', projectId);
  await clickPrimary(page);
  await waitScreen(page, 'package-review');
  await page.waitForSelector('[data-pk-command]');

  // 3: the review facts
  const sent = reviews(srv)[0].body;
  check(reviews(srv).length === 1 && sent.package === 'harmless-mcp@1.2.3' && sent.scope === 'project' && sent.project_id === projectId && sent.server_name === 'harmless-notes'
    && sent.args.join() === '--mode,read-only' && sent.credentials.length === 1 && sent.credentials[0].env === 'HARMLESS_TOKEN' && sent.credentials[0].vault === 'harmless.token',
    'Review sends the package, the chosen project, the name, the arguments and the credential NAMES', 'review body: ' + JSON.stringify(sent));
  check(!('approve_scripts' in sent) && !('passcode' in sent) && !JSON.stringify(sent).includes('"value"'), 'the first Review approves no script and carries no passcode or value', 'review body: ' + JSON.stringify(sent));
  check((await text(page, '[data-cfw-title]')) === 'Review package' && (await text(page, '[data-cfw-stepof]')) === 'Review · 1 of 3', 'the first Review page is "Review package", 1 of 3', await text(page, '[data-cfw-stepof]'));
  const argv = await page.$$eval('[data-pk-command] li', (e) => e.map((x) => x.textContent));
  check(argv[0] === '/usr/bin/python3' && argv.includes('node') && argv.includes('/data/mcp/harmless-mcp/1.2.3/package/dist/index.js') && argv.includes('HARMLESS_TOKEN=harmless.token') && argv.slice(-2).join() === '--mode,read-only', 'the exact command is shown, one argument per line', 'argv: ' + JSON.stringify(argv));
  const pin = await text(page, '[data-pk-pin]');
  check(/1\.2\.3, pinned: it never updates by itself/.test(pin) && pin.includes(DIGEST), 'the version pin and its digest are on the first page', pin);
  check(/registry\.npmjs\.org: https:\/\/registry\.npmjs\.org\/harmless-mcp/.test(await text(page, '[data-pk-source]')), 'the source is on the first page', await text(page, '[data-pk-source]'));
  const reach = await page.$eval('[data-pk-reach]', (e) => [e.dataset.pkReach, e.textContent]);
  check(reach[0] === 'project' && /only/.test(reach[1]) && /no sandbox/.test(reach[1]), 'the reach is on the first page: one project, local code, no sandbox', 'reach: ' + reach);
  check((await text(page, '[data-pk-origin]')).includes('User supplied; not reviewed by Clayrune'), 'the unreviewed-code notice is on the first page', 'origin label missing');
  const cred = await text(page, '[data-pk-credentials]');
  check(cred.includes('harmless.token') && cred.includes('HARMLESS_TOKEN'), 'the credential shows as a Vault entry name and a variable name', cred);
  const risks = await page.$$eval('[data-pk-risk]', (e) => e.map((x) => x.dataset.pkRisk));
  check(['dependencies_installed', 'user_supplied', 'runs_local_code', 'digest_not_safety'].every((c) => risks.includes(c)) && !risks.includes('global_reach'), `every material risk label is shown (${risks.join()})`, 'risks: ' + risks);
  check(!(await has(page, '[data-cfw-body] [data-pk-publisher], [data-cfw-body] [data-cu-dep]')), 'publisher and the dependency list are not in the body', 'inventory leaked into the body');
  await rules(page, 'Review package');
  await shot(page, 'review_facts', width);
  await fits(page, 'Review package');
  const closed = await page.$eval('[data-cfw-details]', (d) => !d.open);
  await openDetails(page);
  check(closed && (await has(page, '[data-cfw-details] [data-pk-publisher]')) && (await has(page, '[data-cfw-details] [data-pk-size]')) && (await has(page, '[data-cfw-details] [data-pk-dependencies-head]')), 'publisher, licence, size and the dependency inventory are under the one Details, closed until opened', 'details content missing');
  const depsOnPage = (await page.$$('[data-cfw-details] [data-cu-dep]')).length;
  check(depsOnPage > 0 && depsOnPage <= 4 && (await has(page, '[data-cfw-details] [data-cfw-pager]')), `dependencies are paged: ${depsOnPage} on this page with a pager`, 'dependency paging wrong ' + depsOnPage);
  check((await text(page, '[data-pk-dependencies-head]')).includes('9 dependency packages installed with it'), 'the dependency summary gives the count and sizes', await text(page, '[data-pk-dependencies-head]'));
  check(await page.$$eval('[data-cfw-details] [data-cu-dep-digest]', (e) => e.length > 0 && e.every((x) => /^sha512-/.test(x.textContent))), 'each listed dependency shows its exact digest', 'a dependency has no digest');
  await shot(page, 'review_details', width);
  await fits(page, 'Review details');

  // 4: scripts, four a page, all off, full bodies
  await clickPrimary(page);
  await waitTitle(page, 'Install steps');
  check((await text(page, '[data-cfw-stepof]')) === 'Review · 2 of 3', 'the install-steps page reads "Review · 2 of 3"', await text(page, '[data-cfw-stepof]'));
  const first = await page.$$eval('[data-cu-script]', (e) => e.map((x) => x.dataset.cuScript));
  check(first.length === 4 && (await has(page, '[data-cfw-pager]')) && /Page 1 of 2/.test(await text(page, '[data-cfw-pageof]')), 'at most four scripts are on a page (4 of 7, Page 1 of 2)', 'scripts on page: ' + first.length);
  check(await page.$$eval('[data-cu-script-tick]', (b) => b.length === 4 && b.every((x) => !x.checked)), 'every install step starts OFF', 'a script started ticked');
  const body1 = await page.$eval('[data-cu-script="node_modules/dep-1#postinstall"] [data-cu-script-body]', (e) => e.textContent);
  check(body1.includes('fetch-1.js --from https://cdn.example.com/1.bin') && body1.includes('second line of script 1'), 'the full body of a script is shown, not a summary', 'body: ' + body1);
  check(/Off: it is not run/.test(await text(page, '[data-cu-script="node_modules/dep-1#postinstall"]')), 'each step says it is off and not run', 'off wording missing');
  check(/not a sandbox/.test(await text(page, '[data-pk-scripts-note]')), 'the scripts note says it is not a sandbox', 'note missing');
  await rules(page, 'Install steps');
  await shot(page, 'scripts', width);
  await fits(page, 'Install steps');
  await page.click('[data-cfw-page="next"]');
  await page.waitForSelector('[data-cu-script="node_modules/sneaky#install"]');
  check((await page.$$('[data-cu-script="node_modules/sneaky#install"] [data-cu-script-tick]')).length === 0 && /cannot be approved/.test(await text(page, '[data-cu-script="node_modules/sneaky#install"]')), 'a script whose body cannot be shown exactly has no checkbox and says why', 'sneaky script has a checkbox');
  await page.click('[data-cfw-page="prev"]');
  await page.check('[data-cu-script-tick="node_modules/dep-2#postinstall"]');
  await page.waitForFunction(() => { const b = document.querySelector('[data-cu-script-tick="node_modules/dep-2#postinstall"]'); return !!b && b.checked; }, null, { timeout: 4000 });
  check(reviews(srv).length === 2 && reviews(srv)[1].body.approve_scripts.join() === 'node_modules/dep-2#postinstall', 'ticking a script reviews again with exactly that script in approve_scripts', 'reviews: ' + JSON.stringify(reviews(srv).map((r) => r.body.approve_scripts)));
  check(/Runs once at Save/.test(await text(page, '[data-cu-script="node_modules/dep-2#postinstall"]')), 'the ticked step says it runs once at Save', 'tick wording missing');

  // 6: Save needs the approval and sends only the stored operation
  await clickPrimary(page);
  await waitTitle(page, 'Approve connection');
  check((await text(page, '[data-cfw-stepof]')) === 'Review · 3 of 3' && /1 install script you ticked runs once, at Save/.test(await text(page, '[data-pk-sum="scripts"]')), 'the approval page counts the ticked script and says when it runs', await text(page, '[data-pk-summary]'));
  check((await text(page, '[data-cfw-primary]')) === 'Save' && await disabled(page, '[data-cfw-primary]'), 'Save is off until the approval is ticked', 'Save enabled before approval');
  check(writes(srv).length === 0, 'nothing has been written yet', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));
  await rules(page, 'Approve');
  await shot(page, 'approve', width);
  await fits(page, 'Approve');
  await page.check('[data-pk-approve]');
  check(!(await disabled(page, '[data-cfw-primary]')), 'ticking the approval turns Save on', 'Save still off');
  await clickPrimary(page);
  await page.waitForSelector('input[id^="hp-passcode-"]');
  await page.click('.modal-content .btn-secondary');
  await page.waitForTimeout(150);
  check(commits(srv).length === 0 && (await screenId(page)) === 'package-review', 'cancelling the passcode prompt sends nothing and keeps the card', 'commits ' + commits(srv).length);
  await clickPrimary(page);
  await passcodePrompt(page, 'wrong-passcode');
  await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
  await page.click('.modal-content .btn-secondary');
  check(commits(srv).length === 1 && (await screenId(page)) === 'package-review' && (await page.$eval('[data-pk-approve]', (c) => c.checked)), 'a wrong passcode reaches the server once; the card and approval stay', 'commits ' + commits(srv).length);
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await waitScreen(page, 'package-result');
  const cbody = commits(srv)[1].body;
  const lastRequest = [...srv.cards.keys()].pop();
  check(Object.keys(cbody).sort().join() === 'fingerprint,passcode,request_id', 'the Save carries only the request id, the fingerprint and the passcode', 'commit keys: ' + Object.keys(cbody));
  check(cbody.request_id === lastRequest && cbody.fingerprint === srv.cards.get(lastRequest).fingerprint, 'the Save names the LAST review (the card with the ticked script)', 'request id ' + cbody.request_id);
  const rtext = await text(page, '[data-pk-result]');
  check((await text(page, '[data-cfw-title]')) === 'Registered' && /Registered/.test(rtext) && !/Connected/.test(rtext) && /starts the first time/.test(rtext), 'the result says Registered and when it starts, never Connected', 'result: ' + rtext);
  check(!(await has(page, '[data-cfw-back]')), 'the result has no Back', 'Back on result');
  await rules(page, 'Result');
  await shot(page, 'result', width);
  await fits(page, 'Result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function invalidateScenario(browser, width, height) {
  console.log(`\n== ${width}px: a changed request is a new review; a stale card is dropped ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(page, { detect: false, vault: null });
  await toApprove(page);
  await page.check('[data-pk-approve]');

  // A script tick after the approval (back on the install-steps page) is a new card: the approval does not carry
  await page.click('[data-pk-prev]');
  await waitTitle(page, 'Install steps');
  const n0 = reviews(srv).length;
  await page.check('[data-cu-script-tick="node_modules/dep-3#postinstall"]');
  await page.waitForFunction(() => { const b = document.querySelector('[data-cu-script-tick="node_modules/dep-3#postinstall"]'); return !!b && b.checked; }, null, { timeout: 4000 });
  await clickPrimary(page); await waitTitle(page, 'Approve connection');
  check(reviews(srv).length === n0 + 1 && reviews(srv)[n0].body.approve_scripts.join() === 'node_modules/dep-3#postinstall', 'a tick after approving reviews again with that script', 'reviews: ' + reviews(srv).length);
  check(!(await page.$eval('[data-pk-approve]', (c) => c.checked)) && await disabled(page, '[data-cfw-primary]'), 'the earlier approval does not carry to the new card: the box is clear and Save is off', 'approval carried over');

  // Back to Permissions and all projects: a different reach is a different card
  await page.click('[data-cfw-back]');
  await waitStep(page, 'permissions');
  await page.check('[data-cfp-scope][value="global"]');
  const n1 = reviews(srv).length;
  await clickPrimary(page);
  await waitScreen(page, 'package-review');
  await page.waitForSelector('[data-pk-command]');
  check(reviews(srv).length === n1 + 1 && reviews(srv)[n1].body.scope === 'global' && !('project_id' in reviews(srv)[n1].body), 'changing the reach reviews again, as global with no project', 'reviews: ' + JSON.stringify(reviews(srv).map((r) => r.body.scope)));
  check((await text(page, '[data-pk-reach]')).includes('every project') && await has(page, '[data-pk-risk="global_reach"]'), 'the new card names every project and carries the global risk label', await text(page, '[data-pk-reach]'));
  await shot(page, 'global', width);
  await fits(page, 'Review package (global)');

  // Back to Setup, change an argument, return: another card
  await page.click('[data-cfw-back]'); await waitStep(page, 'permissions');
  await page.click('[data-cfw-back]'); await waitStep(page, 'setup');
  check((await screenId(page)) === 'package-setup' && (await page.$eval('[data-pk-package]', (i) => i.value).catch(() => 'harmless-mcp')) !== '', 'Back returns to the package setup', await screenId(page));
  if (await has(page, '[data-pk-change]')) { /* on the details page already */ } else { await clickPrimary(page); await waitTitle(page, 'Package details'); }
  await page.fill('[data-pk-args]', '--changed');
  const n2 = reviews(srv).length;
  await clickPrimary(page); await waitStep(page, 'permissions');
  await clickPrimary(page); await waitScreen(page, 'package-review'); await page.waitForSelector('[data-pk-command]');
  check(reviews(srv).length === n2 + 1 && reviews(srv)[n2].body.args.join() === '--changed', 'a changed argument reviews again with the new argument', 'reviews: ' + reviews(srv).length);
  check((await page.$$eval('[data-pk-command] li', (e) => e.map((x) => x.textContent))).slice(-1)[0] === '--changed', 'the command shown is the new one', 'old command shown');

  // A stale card: the server no longer accepts it
  await toApprove(page);
  await page.check('[data-pk-approve]');
  srv.expire = true;
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await page.waitForSelector('[data-pk-review-error]', { timeout: 4000 });
  const msg = await text(page, '[data-cfw-body]');
  check(/changed since you reviewed it/.test(msg) && /Review again/.test(msg), 'a card the server no longer accepts is dropped with the server\'s own words and "Review again"', 'message: ' + msg);
  check((await screenId(page)) === 'package-review' && commits(srv).length === 1 && !(await has(page, '[data-pk-approve]')), 'it offers a new review, not a way to save the old card', 'stale card is still savable');
  await shot(page, 'stale', width);
  srv.expire = false;
  await page.click('[data-pk-rereview]');
  await page.waitForSelector('[data-pk-command]', { timeout: 4000 });
  check(reviews(srv).length > n2 + 1, '"Review again" asks the server for a new card', 'no new review');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function stateScenario(browser, width, height, state, code, title, word) {
  console.log(`\n== ${width}px: result state ${state} ==`);
  const { ctx, page, pageErrors } = await newPage(browser, { srv: makeServer({ commitState: state, commitCode: code }), width, height });
  await toReview(page, { detect: false, vault: null });
  await toApprove(page);
  await page.check('[data-pk-approve]');
  await clickPrimary(page);
  await passcodePrompt(page, PASSCODE);
  await waitScreen(page, 'package-result');
  const t = await text(page, '[data-pk-result]');
  check((await text(page, '[data-cfw-title]')) === title && t.startsWith(word) && !/Connected/.test(t), `the result says "${title}" with the server's words`, 'result: ' + t);
  if (state !== 'registered') check(!/^Registered/.test(t), `"${state}" is never called Registered`, 'called Registered: ' + t);
  await shot(page, `state_${state}`, width);
  await fits(page, `Result ${state}`);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function pypiScenario(browser, width, height) {
  console.log(`\n== ${width}px: PyPI is read, never installed ==`);
  const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
  await toPackageSetup(page);
  await page.fill('[data-pk-package]', 'pypi:acme-mcp');
  const note = await has(page, '[data-pk-pypi]') ? await text(page, '[data-pk-pypi]') : '';
  check(/no Python package installer/.test(note) && /Nothing was installed or saved/.test(note), 'a PyPI name says plainly that Clayrune cannot install it and nothing was', 'pypi notice: ' + note);
  check(await disabled(page, '[data-cfw-primary]'), 'there is no way forward from a PyPI package', 'Continue is on for PyPI');
  await page.click('[data-pk-detect]');
  await page.waitForSelector('[data-pk-detect-state="incomplete"]');
  const line = await text(page, '[data-pk-detect-slot]');
  check(detects(srv).length === 1 && detects(srv)[0].body.kind === 'pypi' && /on PyPI/.test(line) && !/on npm/.test(line), 'Detect asks for kind pypi and the line says PyPI, not npm', JSON.stringify(detects(srv).map((d) => d.body)) + ' ' + line);
  check(await disabled(page, '[data-cfw-primary]') && reviews(srv).length === 0 && commits(srv).length === 0 && writes(srv).length === 0, 'detection does not enable Continue, call the npm review or write anything', 'requests: ' + JSON.stringify(srv.log.map((r) => r.path)));
  await shot(page, 'pypi', width);
  await fits(page, 'PyPI');
  await page.fill('[data-pk-package]', 'missing-pkg');
  await page.click('[data-pk-detect]');
  await page.waitForSelector('[data-pk-detect-state="error"]');
  const miss = await text(page, '[data-pk-detect-slot]');
  check(/not found on npm/.test(miss) && !(await disabled(page, '[data-cfw-primary]')), 'an npm package the registry does not have says so; typing it by hand is still allowed', miss);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function bigTextScenario(browser, width, height) {
  console.log(`\n== ${width}px: 200% text ==`);
  const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
  await toReview(page, { detect: true });
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await fits(page, 'Review package at 200% text');
  await openDetails(page);
  await fits(page, 'Review details at 200% text');
  await clickPrimary(page); await waitTitle(page, 'Install steps');
  await fits(page, 'Install steps at 200% text');
  await shot(page, 'scripts_200pct', width);
  await ctx.close();
}

const browser = await chromium.launch({ headless: true });
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await mainScenario(browser, w, h);
    await invalidateScenario(browser, w, h);
    await stateScenario(browser, w, h, 'pending_runtime', 'wrapper_missing', 'Saved, cannot run yet', 'Saved, cannot run yet');
    await stateScenario(browser, w, h, 'setup_failed', 'unpack_failed', 'Saved, setup failed', 'Saved, setup failed');
    await pypiScenario(browser, w, h);
    await bigTextScenario(browser, w, h);
  }
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
