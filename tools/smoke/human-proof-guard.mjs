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

const calls = { config: [], character: [], workflow: [], voice: [], identity: [] };
let configResponder = (body) => ({ status: 200, body: {} });
let characterResponder = (body) => ({ status: 200, body: { ok: true } });
let workflowResponder = (body) => ({
  status: 200,
  body: { ok: true, workflow: { ...body, id: 'wf_smoke_1' } },
});
let voiceResponder = (body) => ({ status: 200, body: { voice: '## Voice\n\nSpeaks in short declarative sentences.' } });
let identityResponder = (body) => ({ status: 200, body: { agent_name: 'Bolt', avatar: 'fig:smith' } });

// Dave review of 78052d4 (item D): a canned character-ready Claydo reply, so
// R3 below can reach the real "Save character…" button through the actual
// SSE parse path (_claydoParseMarkers / _claydoLastFenced) instead of calling
// _claydoOpenSavePanel directly.
const CLAYDO_READY_ANSWER = 'Here is your agent.\n\n[clayrune:character-ready name="builder"]\n\n'
  + '```\n---\nname: builder\ndescription: builds things\n---\nYou are a builder.\n```';

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
  if (path === '/api/avatars') return json({ figures: [] });

  if (path === '/api/config' && method === 'GET') return json({});
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
  if (path === '/api/characters/voice' && method === 'POST') {
    const body = postJson();
    calls.voice.push(body);
    const r = voiceResponder(body);
    return json(r.body, r.status);
  }
  if (path === '/api/characters/identity' && method === 'POST') {
    const body = postJson();
    calls.identity.push(body);
    const r = identityResponder(body);
    return json(r.body, r.status);
  }
  // Canned SSE stream: one 'done' event carrying a character-ready reply, so
  // R3 below can drive the real ready-card "Save character…" button.
  if (path === '/api/guide/stream' && method === 'POST') {
    const sse = `data: ${JSON.stringify({ type: 'done', answer: CLAYDO_READY_ANSWER })}\n\n`;
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse });
  }
  return route.abort();
});

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.humanProofFetch === 'function'
    && typeof window.saveSetting === 'function'
    && typeof window.openPersonaEditor === 'function'
    && typeof window.openWorkflowBuilder === 'function'
    && typeof window.openSettings === 'function'
    && typeof window.drillSettings === 'function'
    && typeof window.openClaydo === 'function', { timeout: 20000 });

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
  // Dave review of 78052d4 (item D): R1-R4 below need REAL clicks/keypresses
  // against the modal's own Confirm/Cancel buttons — the helpers above drive
  // window._hpSubmit/_hpCancel directly, which proves the fetch/DOM wiring
  // but not that a human's mouse can actually reach the button.
  const hpClickConfirm = async () => {
    await page.waitForSelector('[data-modal-id^="__human-proof-"] button.btn-add', { timeout: 5000 });
    await page.click('[data-modal-id^="__human-proof-"] button.btn-add');
  };
  const hpClickCancel = async () => {
    await page.waitForSelector('[data-modal-id^="__human-proof-"] button.btn-secondary', { timeout: 5000 });
    await page.click('[data-modal-id^="__human-proof-"] button.btn-secondary');
  };
  const hpFillPasscodeReal = (passcode) => page.fill('[id^="hp-passcode-__human-proof-"]', passcode);

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

  // ── Dave review of 78052d4 (item D): click-level evidence for R1-R4 ────────
  // Everything above drives the modal programmatically (window._hpSubmit /
  // _hpCancel) to pin the fetch/DOM wiring quickly. These four re-run the
  // same scenarios through real Playwright mouse clicks and key presses —
  // page.click (no force) fails if the target is covered, the way a human's
  // click would.

  // R1: persona editor → open the prompt → real click on Confirm.
  calls.character.length = 0;
  characterResponder = () => ({ status: 200, body: { ok: true } });
  await page.evaluate(() => window.openPersonaEditor(null, 'global', 'builder'));
  await page.waitForSelector('#pe-save', { timeout: 5000 });
  await page.click('#pe-save');
  await hpModal();
  await hpFillPasscodeReal(FIXTURE_PASSCODE);
  await hpClickConfirm();
  await hpGone();
  const r1Call = calls.character[calls.character.length - 1];
  (r1Call && r1Call.passcode === FIXTURE_PASSCODE)
    ? ok('R1: persona editor Confirm — a real mouse click (not force) submits the request')
    : fail(`R1: real Confirm click did not submit: ${JSON.stringify(r1Call)}`);
  await page.evaluate(() => document.querySelector('.persona-editor')?.remove());

  // R2: press Escape (real key press) — no request, and the caller's Save
  // re-enables (it was disabled the instant Save was clicked).
  calls.character.length = 0;
  await page.evaluate(() => window.openPersonaEditor(null, 'global', 'builder'));
  await page.waitForSelector('#pe-save', { timeout: 5000 });
  await page.click('#pe-save');
  await hpModal();
  const disabledWhilePending = await page.evaluate(() => document.getElementById('pe-save').disabled);
  await page.keyboard.press('Escape');
  await hpGone();
  calls.character.length === 0
    ? ok('R2: Escape sends no PUT /api/characters/global/builder')
    : fail(`R2: Escape still sent a request: ${JSON.stringify(calls.character)}`);
  const reenabled = await page.evaluate(() => document.getElementById('pe-save').disabled === false);
  (disabledWhilePending && reenabled)
    ? ok("R2: Escape re-enables the caller's Save button (disabled while pending, enabled after)")
    : fail(`R2: Save button state wrong (pending disabled=${disabledWhilePending}, after enabled=${reenabled})`);
  await page.evaluate(() => document.querySelector('.persona-editor')?.remove());

  // R3: Claydo save panel — 0 requests to voice/identity before a click,
  // then a real click on Generate raises the prompt. Drives the actual
  // ready-card button through a canned SSE character-ready reply, not a
  // direct call into _claydoOpenSavePanel.
  await page.evaluate(() => window.openClaydo());
  await page.waitForSelector('#claydo-input', { timeout: 5000 });
  await page.fill('#claydo-input', 'hire me a builder');
  await page.click('#claydo-send');
  await page.waitForSelector('.claydo-ready-card', { timeout: 10000 });
  const saveBtnHandle = await page.evaluateHandle(() =>
    Array.from(document.querySelectorAll('.claydo-ready-card button')).find(b => b.textContent.includes('Save character')));
  calls.voice.length = 0;
  calls.identity.length = 0;
  await saveBtnHandle.asElement().click();
  await page.waitForSelector('#claydo-save-voice-regen', { timeout: 5000 });
  (calls.voice.length === 0 && calls.identity.length === 0)
    ? ok('R3: opening the Claydo save panel sends 0 requests to /api/characters/voice or /identity')
    : fail(`R3: an unrequested call fired before any click: voice=${JSON.stringify(calls.voice)} identity=${JSON.stringify(calls.identity)}`);
  await page.click('#claydo-save-voice-regen');
  await hpModal();
  ok('R3: a real click on Generate voice raises the passcode prompt');
  await hpClickCancel();
  await hpGone();
  await page.evaluate(() => document.querySelector('.claydo-save-panel')?.remove());

  // R4: toggle a setting, cancel the prompt with a real click, and the
  // switch reverts to its original (pre-toggle) value.
  await page.evaluate(() => window.openSettings());
  await page.click('button.settings-list-row:has-text("Appearance")');
  await page.click('button.settings-sub-row:has-text("Theme & display")');
  const toggleSel = '[data-cat="appearance"] .settings-toggle';
  await page.waitForSelector(toggleSel, { timeout: 5000 });
  const beforeOn = await page.evaluate((sel) => document.querySelector(sel).classList.contains('on'), toggleSel);
  await page.click(toggleSel);
  const duringOn = await page.evaluate((sel) => document.querySelector(sel).classList.contains('on'), toggleSel);
  await hpModal();
  await hpClickCancel();
  await hpGone();
  // saveSetting's cancel branch calls _renderSettings() without awaiting it
  // (fire-and-forget) — it re-fetches /api/config before rebuilding the
  // pane, so the reverted class lands a tick after hpGone() resolves, not
  // synchronously with it.
  await page.waitForFunction(({ sel, want }) => {
    const el = document.querySelector(sel);
    return el && el.classList.contains('on') === want;
  }, { sel: toggleSel, want: beforeOn }, { timeout: 5000 }).catch(() => {});
  const afterOn = await page.evaluate((sel) => document.querySelector(sel)?.classList.contains('on'), toggleSel);
  (duringOn === !beforeOn && afterOn === beforeOn)
    ? ok(`R4: toggle flips optimistically (${beforeOn}->${duringOn}) then a real Cancel click reverts it (${afterOn})`)
    : fail(`R4: switch state wrong (before=${beforeOn}, duringPending=${duringOn}, after=${afterOn})`);
  await page.evaluate(() => closeModalById('__settings'));

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
