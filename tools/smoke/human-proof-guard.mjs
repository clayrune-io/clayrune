#!/usr/bin/env node
/**
 * MC-995 — the shared human-proof passcode modal (static/js/human-proof-modal.js)
 * across three real call sites: a settings toggle (PUT /api/config), a
 * character edit (PUT /api/characters/<scope>/<name>), and a workflow save
 * (POST /api/workflows). All three route through the same `humanProofFetch`
 * — this smoke pins the UI half at the wire level (real modal DOM, real
 * fetch interception) the way secrets-vault-passcode.mjs pins the vault-lock
 * forms, not just that the routes exist server-side (tests/test_human_proof_guard.py
 * covers that half).
 *
 * Covers per case: cancel sends no request, wrong passcode re-prompts with
 * the server's error text and sends nothing that succeeds, correct passcode
 * sends the real body plus `passcode` and resolves. One case also runs at a
 * phone viewport (<=960px) to confirm the modal isn't a desktop-only layout.
 *
 * Hermetic, like secrets-vault-passcode.mjs: page + static assets served
 * from THIS checkout, /api/* canned, everything else aborted. No running MC
 * server, no real passcode.
 *
 * RUN: node tools/smoke/human-proof-guard.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_wf';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// page.evaluate() has no built-in timeout (unlike click/fill/waitForSelector),
// so a hang inside browser-side async code would otherwise stall this whole
// script forever with no diagnostic. Race every risky evaluate against this.
const withTimeout = (promise, ms, label) => Promise.race([
  promise,
  new Promise((_, rej) => setTimeout(() => rej(new Error(`TIMEOUT after ${ms}ms: ${label}`)), ms)),
]);

const FIXTURE_PASSCODE = 'smoke-dash-passcode';

const CHAR_RECORD = {
  name: 'builder', scope: 'global', description: 'builds things',
  body: 'You are a builder.', agent_name: '', avatar: '', skills: [],
  engine: {},
};

const calls = { config: [], character: [], workflow: [] };
let configResponder = (body) => ({ status: 200, body: {} });
let characterResponder = (body) => ({ status: 200, body: { ok: true } });
let workflowResponder = (body) => ({
  status: 200,
  body: { ok: true, workflow: { ...body, id: 'wf_smoke_1' } },
});

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
page.on('dialog', (d) => { fail('unexpected native dialog: ' + d.message()); d.dismiss(); });
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const method = req.method();
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });
  const postJson = () => { try { return JSON.parse(req.postData() || '{}'); } catch (_) { return {}; } };

  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
      body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });

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

  if (path === '/api/config' && method === 'PUT') {
    const body = postJson();
    calls.config.push(body);
    const r = configResponder(body);
    return json(r.body, r.status);
  }
  if (path === '/api/characters/global/builder' && method === 'GET') return json(CHAR_RECORD);
  if (path === '/api/characters/global/builder' && method === 'PUT') {
    const body = postJson();
    calls.character.push(body);
    const r = characterResponder(body);
    return json(r.body, r.status);
  }
  if (path === '/api/workflows' && method === 'GET') return json([]);
  if (path === '/api/workflows' && method === 'POST') {
    const body = postJson();
    calls.workflow.push(body);
    const r = workflowResponder(body);
    return json(r.body, r.status);
  }
  return route.abort();
});

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.humanProofFetch === 'function'
    && typeof window.saveSetting === 'function'
    && typeof window.openPersonaEditor === 'function'
    && typeof window.openWorkflowBuilder === 'function', { timeout: 20000 });

  // ── helpers against the one shared modal ──────────────────────────────────
  // Both the persona-editor panel and the workflow builder are appended
  // outside the modal layer's own z-index management, so a raw Playwright
  // click can find its target obscured. Address the modal by its own
  // modalId and drive it through the SAME window._hpSubmit/_hpCancel the
  // onclick attributes call — this still exercises the real fetch/DOM path,
  // just without depending on stacking order the modal itself doesn't own.
  const hpModal = () => page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 });
  const hpModalId = () => page.evaluate(() => document.querySelector('[data-modal-id^="__human-proof-"]').dataset.modalId);
  const hpFillAndSubmit = async (passcode) => {
    await page.waitForSelector('[id^="hp-passcode-__human-proof-"]', { timeout: 5000 });
    const modalId = await hpModalId();
    await page.evaluate(({ modalId, passcode }) => {
      document.getElementById(`hp-passcode-${modalId}`).value = passcode;
      window._hpSubmit(modalId);
    }, { modalId, passcode });
  };
  const hpCancel = async () => {
    await page.waitForSelector('[id^="hp-passcode-__human-proof-"]', { timeout: 5000 });
    const modalId = await hpModalId();
    await page.evaluate((modalId) => window._hpCancel(modalId), modalId);
  };
  const hpErrorText = () => page.evaluate(() => {
    const el = document.querySelector('[data-modal-id^="__human-proof-"]');
    return el ? el.textContent : '';
  });
  const hpGone = () => page.waitForFunction(
    () => document.querySelector('[data-modal-id^="__human-proof-"]') === null, { timeout: 5000 });

  // ── 1. Settings toggle: PUT /api/config ──────────────────────────────────
  calls.config.length = 0;
  await page.evaluate(() => { window.__hpResult = null; window.saveSetting('usage_bar_enabled', true).then(r => { window.__hpResult = { done: true, r }; }); });
  await hpModal();
  await hpCancel();
  await hpGone();
  calls.config.length === 0
    ? ok('settings: Cancel sends no PUT /api/config')
    : fail(`settings: Cancel still sent a request: ${JSON.stringify(calls.config)}`);

  configResponder = () => ({ status: 403, body: { error: 'bad_passcode' } });
  await page.evaluate(() => { window.saveSetting('usage_bar_enabled', true); });
  await hpModal();
  await hpFillAndSubmit('wrong-one');
  await page.waitForFunction(
    () => (document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || '').includes('Wrong dashboard passcode.'),
    { timeout: 5000 });
  ok('settings: wrong passcode re-prompts with "Wrong dashboard passcode."');
  calls.config.length === 1 && calls.config[0].passcode === 'wrong-one'
    ? ok('settings: the wrong-passcode attempt still carried it in the body (server, not client, refused it)')
    : fail(`settings: expected exactly one failed attempt carrying the typed passcode, got ${JSON.stringify(calls.config)}`);

  configResponder = () => ({ status: 200, body: {} });
  await hpFillAndSubmit(FIXTURE_PASSCODE);
  await hpGone();
  const lastConfigCall = calls.config[calls.config.length - 1];
  (lastConfigCall.usage_bar_enabled === true && lastConfigCall.passcode === FIXTURE_PASSCODE)
    ? ok('settings: correct passcode sends the real body + passcode, modal closes')
    : fail(`settings: correct-passcode call wrong: ${JSON.stringify(lastConfigCall)}`);

  // ── 2. Character edit: PUT /api/characters/global/builder ────────────────
  calls.character.length = 0;
  await page.evaluate(() => window.openPersonaEditor(null, 'global', 'builder'));
  await page.waitForSelector('#pe-save', { timeout: 5000 });
  await page.click('#pe-save');
  await hpModal();
  await hpCancel();
  await hpGone();
  calls.character.length === 0
    ? ok('character edit: Cancel sends no PUT /api/characters/global/builder')
    : fail(`character edit: Cancel still sent a request: ${JSON.stringify(calls.character)}`);

  characterResponder = () => ({ status: 403, body: { error: 'bad_passcode' } });
  await page.click('#pe-save');
  await hpModal();
  await hpFillAndSubmit('wrong-one');
  await page.waitForFunction(
    () => (document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || '').includes('Wrong dashboard passcode.'),
    { timeout: 5000 });
  ok('character edit: wrong passcode re-prompts with "Wrong dashboard passcode."');

  characterResponder = () => ({ status: 200, body: { ok: true } });
  await hpFillAndSubmit(FIXTURE_PASSCODE);
  await hpGone();
  const lastCharCall = calls.character[calls.character.length - 1];
  (lastCharCall.description === CHAR_RECORD.description && lastCharCall.passcode === FIXTURE_PASSCODE)
    ? ok('character edit: correct passcode sends the real body + passcode, modal closes')
    : fail(`character edit: correct-passcode call wrong: ${JSON.stringify(lastCharCall)}`);
  await page.evaluate(() => document.querySelector('.persona-editor')?.remove());

  // ── 3. Workflow save: POST /api/workflows ─────────────────────────────────
  await page.evaluate((pid) => {
    const host = document.createElement('div');
    host.id = 'wfb-inline-host-' + pid;
    document.body.appendChild(host);
  }, PID);
  await withTimeout(page.evaluate((pid) => window.openWorkflowBuilder(null, pid), PID), 8000, 'openWorkflowBuilder');
  await page.waitForSelector('#wfb-name', { timeout: 5000 });
  await page.fill('#wfb-name', 'Smoke workflow');
  await page.evaluate((pid) => {
    const def = window._wfEntry()._wf.def;
    def.nodes.push({ type: 'agent', name: 'triage-agent', x: 40, y: 40,
      project_id: pid, character: 'global:builder', prompt: 'triage', outcomes: [] });
  }, PID);
  await page.dispatchEvent('#wfb-name', 'change'); // marks dirty like a real edit would

  // _wfSave() awaits humanProofFetch() internally, which does not resolve
  // until the modal is cancelled/submitted below -- fire it without awaiting
  // its own completion (same fire-and-forget shape as saveSetting above).
  const fireWfSave = () => page.evaluate(() => { window._wfSave(); });

  calls.workflow.length = 0;
  await fireWfSave();
  await hpModal();
  await hpCancel();
  await hpGone();
  calls.workflow.length === 0
    ? ok('workflow save: Cancel sends no POST /api/workflows')
    : fail(`workflow save: Cancel still sent a request: ${JSON.stringify(calls.workflow)}`);

  workflowResponder = () => ({ status: 403, body: { error: 'bad_passcode' } });
  await fireWfSave();
  await hpModal();
  await hpFillAndSubmit('wrong-one');
  await page.waitForFunction(
    () => (document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || '').includes('Wrong dashboard passcode.'),
    { timeout: 5000 });
  ok('workflow save: wrong passcode re-prompts with "Wrong dashboard passcode."');

  workflowResponder = (body) => ({ status: 200, body: { ok: true, workflow: { ...body, id: 'wf_smoke_1' } } });
  await hpFillAndSubmit(FIXTURE_PASSCODE);
  await hpGone();
  const lastWfCall = calls.workflow[calls.workflow.length - 1];
  (lastWfCall.name === 'Smoke workflow' && lastWfCall.passcode === FIXTURE_PASSCODE
    && Array.isArray(lastWfCall.nodes) && lastWfCall.nodes.some(n => n.name === 'triage-agent'))
    ? ok('workflow save: correct passcode sends the real definition + passcode, modal closes')
    : fail(`workflow save: correct-passcode call wrong: ${JSON.stringify(lastWfCall)}`);

  // ── 4. Phone viewport (<=960px): the modal must still render and submit ──
  await ctx.setDefaultTimeout(5000);
  await page.setViewportSize({ width: 390, height: 844 });
  calls.config.length = 0;
  configResponder = () => ({ status: 200, body: {} });
  await page.evaluate(() => { window.saveSetting('usage_bar_enabled', false); });
  await hpModal();
  const modalBox = await page.evaluate(() => {
    const el = document.querySelector('[data-modal-id^="__human-proof-"]');
    const r = el.getBoundingClientRect();
    return { width: r.width, right: r.right, left: r.left };
  });
  (modalBox.left >= 0 && modalBox.right <= 390)
    ? ok(`phone viewport (390px): modal stays on-screen (left=${modalBox.left.toFixed(0)}, right=${modalBox.right.toFixed(0)})`)
    : fail(`phone viewport: modal runs off-screen: ${JSON.stringify(modalBox)}`);
  await hpFillAndSubmit(FIXTURE_PASSCODE);
  await hpGone();
  calls.config.length === 1 && calls.config[0].passcode === FIXTURE_PASSCODE
    ? ok('phone viewport: correct passcode still submits successfully')
    : fail(`phone viewport: submit failed: ${JSON.stringify(calls.config)}`);

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
