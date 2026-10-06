#!/usr/bin/env node
/**
 * MC-1053 (backlog 34aac480) — the MCP panel asks for the dashboard passcode before it adds or
 * changes an MCP server. Three call sites, all through the shared `humanProofFetch` modal:
 *   - New server   POST /api/mcp                (static/js/mcp.js saveMCPServer, isNew)
 *   - Edit server  PUT  /api/mcp/<scope>/<name> (saveMCPServer, edit)
 *   - URL install  POST /api/mcp/url/install    (_mcpUrlInstall; an SSE stream, `stream: true`)
 * Per case: Cancel sends no request, a wrong passcode re-prompts with "Wrong dashboard passcode."
 * and the (canned) server writes nothing, the right passcode sends the real body plus `passcode`
 * and the panel finishes. Run at 1440 and at 390 wide: the modal must not be desktop-only.
 *
 * Hermetic, like human-proof-guard.mjs: page + static assets from THIS checkout, /api/* canned,
 * everything else aborted. The canned server enforces FIXTURE_PASSCODE the way the real route
 * does (403 bad_passcode), so "writes nothing" is read off its own record of what it accepted.
 *
 * RUN: node tools/smoke/mcp-panel-gate.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';
const FIXTURE_PASSCODE = 'smoke-dash-passcode';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// Server-side state of the canned MCP routes: every attempt, and what was accepted.
const attempts = [];
const written = new Set();

const EXISTING = { name: 'plain', scope: 'global', transport: 'stdio', config: { command: 'node', args: ['x.js'], env: {} } };

async function run(viewport, label) {
  console.log(`\n── ${label} (${viewport.width}px) ──`);
  attempts.length = 0; written.clear();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  page.on('dialog', (d) => { fail('unexpected native dialog: ' + d.message()); d.dismiss(); });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const postJson = () => { try { return JSON.parse(req.postData() || '{}'); } catch (_) { return {}; } };

    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    if (path.startsWith('/static/')) {
      const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
      if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
        const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
          : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
      }
      return route.abort();
    }
    if (path === '/api/projects') return json([]);
    if (path === '/api/floor') return json({ rooms: [], quiet: [], bench: [], counts: {} });
    if (path === '/api/secrets') return json({ secrets: [], locked: false });
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/avatars') return json({ figures: [] });
    if (path === '/api/config') return json({});
    if (path === '/api/mcp' && method === 'GET') return json([]);
    if (path === '/api/mcp/global/plain' && method === 'GET') return json(EXISTING);

    // The three gated routes. Same refusal the real `_require_human_passcode` gives.
    const gated = (name, then) => {
      const body = postJson();
      attempts.push({ route: name, method, body });
      if (body.passcode !== FIXTURE_PASSCODE) return json({ error: 'bad_passcode' }, 403);
      return then(body);
    };
    if (path === '/api/mcp' && method === 'POST')
      return gated('create', (b) => { written.add(b.name); return json({ name: b.name, scope: b.scope }, 201); });
    if (path === '/api/mcp/global/plain' && method === 'PUT')
      return gated('update', (b) => { written.add('plain:edited'); return json({ name: 'plain' }); });
    if (path === '/api/mcp/url/preview' && method === 'POST')
      return json({
        kind: 'npm', classified: { kind: 'npm', package: '@scope/mcp-x' }, servers: { '@scope/mcp-x': { command: 'npx', args: ['-y', '@scope/mcp-x'] } },
        name_hint: 'mcp-x', source_tier: 0, secrets: [], install_commands: [], github: { available: false },
        audit: { available: false, reason: 'npm' }, scan: { available: false, reason: 'npm' }, install_dir: null, sha: null,
      });
    if (path === '/api/mcp/url/install' && method === 'POST')
      return gated('install', (b) => {
        written.add(b.name);
        const sse = 'data: ' + JSON.stringify({ type: 'start' }) + '\n\n'
          + 'data: ' + JSON.stringify({ type: 'done', record: { name: b.name, scope: b.scope } }) + '\n\n';
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse });
      });
    return route.abort();
  });

  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.humanProofFetch === 'function'
      && typeof window.openMCPEditor === 'function' && typeof window._mcpUrlInstall === 'function', { timeout: 20000 });

    const hpModalId = () => page.evaluate(() => document.querySelector('[data-modal-id^="__human-proof-"]')?.dataset.modalId || null);
    const hpOpen = () => page.waitForSelector('[id^="hp-passcode-__human-proof-"]', { timeout: 5000 });
    const hpGone = () => page.waitForFunction(() => document.querySelector('[data-modal-id^="__human-proof-"]') === null, { timeout: 5000 });
    const hpType = async (passcode) => {
      await hpOpen();
      await page.fill('[id^="hp-passcode-__human-proof-"]', passcode);
      await page.click('[data-modal-id^="__human-proof-"] button.btn-add');
    };
    const hpCancel = async () => { await hpOpen(); await page.click('[data-modal-id^="__human-proof-"] button.btn-secondary'); };
    const hpWrongShown = () => page.waitForFunction(
      () => (document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || '').includes('Wrong dashboard passcode.'), { timeout: 5000 });
    const inViewport = async (sel) => {
      const b = await page.evaluate((s) => { const r = document.querySelector(s)?.getBoundingClientRect(); return r && { l: r.left, r: r.right, t: r.top, b: r.bottom }; }, sel);
      return !!b && b.l >= -1 && b.r <= viewport.width + 1 && b.t >= -1 && b.b <= viewport.height + 1;
    };

    // The editor save, new + edit: the same flow, driven by the real Create/Save button.
    const editorCase = async (what, openArgs, fill, route, expectWritten, sel) => {
      attempts.length = 0; written.clear();
      await page.evaluate((a) => window.openMCPEditor(...a), openArgs);
      await page.waitForSelector('#me-save', { timeout: 5000 });
      await fill();
      await page.click('#me-save');
      await hpOpen();
      (await inViewport('[data-modal-id^="__human-proof-"] .modal-content'))
        ? ok(`${what}: the passcode prompt fits the viewport`) : fail(`${what}: the passcode prompt overflows the viewport`);
      await hpCancel(); await hpGone();
      attempts.length === 0 ? ok(`${what}: Cancel sends no request`) : fail(`${what}: Cancel still sent ${JSON.stringify(attempts)}`);

      await page.click('#me-save');
      await hpType('wrong-one');
      await hpWrongShown();
      ok(`${what}: a wrong passcode re-prompts with "Wrong dashboard passcode."`);
      (attempts.length === 1 && attempts[0].route === route && written.size === 0)
        ? ok(`${what}: the refused attempt wrote nothing`) : fail(`${what}: wrong-passcode state ${JSON.stringify({ attempts, written: [...written] })}`);

      await hpType(FIXTURE_PASSCODE);
      await hpGone();
      const last = attempts[attempts.length - 1];
      (last.body.passcode === FIXTURE_PASSCODE && written.has(expectWritten))
        ? ok(`${what}: the right passcode sends the real body and writes`) : fail(`${what}: right-passcode state ${JSON.stringify({ last, written: [...written] })}`);
      await page.waitForFunction((id) => !document.querySelector(`[data-modal-id="${id}"]`), sel, { timeout: 5000 })
        .then(() => ok(`${what}: the editor closes after the save`)).catch(() => fail(`${what}: the editor stayed open after a good save`));
    };

    await editorCase('new server', ['global', '', null, true],
      async () => { await page.fill('#me-name', 'plain'); await page.fill('#me-command', 'node'); await page.fill('#me-args', 'x.js'); },
      'create', 'plain', '__mcp_new');
    await editorCase('edit server', ['global', 'plain', null, false],
      async () => { await page.fill('#me-args', 'y.js'); },
      'update', 'plain:edited', '__mcp_global_plain');

    // URL install: preview first (no passcode: it writes nothing), then Install asks.
    attempts.length = 0; written.clear();
    await page.evaluate(() => window.openMCPEditor('global', '', null, true));
    await page.waitForSelector('#me-mode-url', { timeout: 5000 });
    await page.click('#me-mode-url');
    await page.fill('#me-url-input', '@scope/mcp-x');
    await page.click('#me-url-preview-btn');
    const installBtn = '#me-url-mode button.btn-add:has-text("Install")';
    await page.waitForSelector(installBtn, { timeout: 5000 });
    await page.click(installBtn);
    await hpOpen();
    await hpCancel(); await hpGone();
    (attempts.length === 0 && await page.isVisible(installBtn))
      ? ok('install: Cancel sends no request and the preview stays') : fail(`install: Cancel state ${JSON.stringify(attempts)}`);
    await page.click(installBtn);
    await hpType('wrong-one');
    await hpWrongShown();
    ok('install: a wrong passcode re-prompts with "Wrong dashboard passcode."');
    (attempts.length === 1 && written.size === 0 && await page.isVisible(installBtn))
      ? ok('install: the refused attempt installed nothing and stayed on the preview') : fail(`install: wrong-passcode state ${JSON.stringify({ attempts, written: [...written] })}`);
    await hpType(FIXTURE_PASSCODE);
    await hpGone();
    await page.waitForFunction(() => (document.querySelector('#me-url-mode')?.textContent || '').includes('Installed'), { timeout: 5000 })
      .then(() => ok('install: the right passcode streams through to "Installed"')).catch(() => fail('install: never reached the Installed state'));
    written.has('mcp-x') && attempts[attempts.length - 1].body.passcode === FIXTURE_PASSCODE
      ? ok('install: the stream request carried the passcode and wrote') : fail(`install: ${JSON.stringify({ attempts, written: [...written] })}`);

    pageErrors.length === 0 ? ok('no uncaught page errors') : pageErrors.forEach((e) => fail('uncaught: ' + e));
  } finally {
    await ctx.close();
    await browser.close();
  }
}

await run({ width: 1440, height: 900 }, 'desktop');
await run({ width: 390, height: 844 }, 'phone');

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
