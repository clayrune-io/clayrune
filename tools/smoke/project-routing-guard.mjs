#!/usr/bin/env node
/** MC-1080: real Create/Import/workspace handlers and human-proof modal.
 * Hermetic desktop/phone wire smoke: no server, real settings or credentials.
 * Run: node tools/smoke/project-routing-guard.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const STATIC = loadStaticJsCss(ROOT);
const BASE = JSON.parse(readFileSync(resolve(ROOT, 'tools/smoke/fixtures/projects.json'), 'utf8'))[0];
const ORIGIN = 'http://mc.smoke.test';
const PROOF = 'routing-smoke-proof';
const browser = await chromium.launch();

async function scenario(width) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 600, hasTouch: width < 600 });
  const page = await ctx.newPage();
  page.setDefaultTimeout(6000);
  const errors = [], alerts = [], calls = [], accepted = [];
  const projects = [{ ...BASE, project_path: '/smoke/original' }];
  let refusePath = '', configured = true;
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', async d => { alerts.push(d.message()); await d.dismiss(); });
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    // Unrelated optional diagram CDN is outside this hermetic routing smoke.
    if (url.hostname === 'cdn.jsdelivr.net' && path.includes('/mermaid@')) return route.fulfill({
      status: 200, contentType: 'text/javascript', headers: { 'Access-Control-Allow-Origin': '*' },
      body: 'export default { initialize() {}, render: async () => ({ svg: "<svg></svg>" }) };',
    });
    if (path === '/') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return json(projects);
    if (path === '/api/config') return json({ setup_completed: true });
    if (path === '/api/characters') return json([]);
    if (path === '/api/avatars') return json({ figures: [] });
    if (path === '/api/local-auth/status') return json({ configured });
    if (path === '/api/local-auth/set') { configured = true; return json({ ok: true }); }
    if (path === '/api/browse/folders') return json({
      path: url.searchParams.get('path') || '/smoke', parent: '/smoke', home: '/smoke', folders: [],
    });
    if (path === '/api/claude-import/scan') return json({ ok: true, total: 2, skipped: {}, candidates: [
      { id: 'import_a', name: 'Import A', path: '/smoke/import-a', session_count: 1 },
      { id: 'import_b', name: 'Import B', path: '/smoke/import-b', session_count: 1 },
    ] });
    const m = path.match(/^\/api\/project\/([^/]+)$/);
    if (m && req.method() === 'POST') {
      const body = JSON.parse(req.postData()), id = decodeURIComponent(m[1]);
      calls.push({ id, body });
      if (body.passcode !== PROOF) return json({ error: 'bad_passcode' }, 403);
      if (body.project_path === refusePath && refusePath) return json({ error: 'Workspace test refusal' }, 409);
      accepted.push({ id, body });
      let p = projects.find(p => p.id === id);
      if (!p) { p = { ...BASE, id }; projects.push(p); }
      const { passcode, ...fields } = body;
      Object.assign(p, fields);
      if (!p.project_path) p.project_path = `/smoke/generated/${id}`;
      return json({ ok: true, id });
    }
    return route.abort();
  });

  const prompt = async () => {
    await page.waitForSelector('[data-modal-id^="__human-proof-"]');
    const id = await page.locator('[data-modal-id^="__human-proof-"]').getAttribute('data-modal-id');
    return page.locator(`[data-modal-id="${id}"]`);
  };
  const cancel = async () => {
    const p = await prompt();
    await p.locator('.btn-secondary').click();
    await p.waitFor({ state: 'detached' });
  };
  const approve = async (proof = PROOF) => {
    const p = await prompt();
    await p.locator('[id^="hp-passcode-"]').fill(proof);
    await p.locator('.btn-add').click();
    if (proof === PROOF) await p.waitFor({ state: 'detached' });
    else await p.locator('[id^="hp-passcode-"]').waitFor();
  };
  const startCreate = async (id, path) => {
    await page.evaluate(() => window.openNewProjectForm());
    await page.locator('#new-proj-name').fill(id);
    await page.locator('#new-proj-id').fill(id);
    await page.locator('#new-proj-path').fill(path);
    await page.locator('[data-modal-id="__new_project"] button[onclick="createProject()"]').click();
    await prompt();
  };

  try {
    await page.goto(ORIGIN, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => allProjects.some(p => p.id === 'smoke_alpha'));

    await startCreate('human_create', '/smoke/create');
    assert.equal(calls.length, 0, 'opening Create sends no routing write');
    await cancel();
    assert.equal(calls.length, 0, 'cancel Create sends no write');
    assert.equal(await page.locator('#new-proj-path').inputValue(), '/smoke/create', 'Create draft preserved');
    await page.locator('[data-modal-id="__new_project"] button[onclick="createProject()"]').click();
    await approve('wrong-proof');
    assert.equal(accepted.length, 0, 'wrong passcode cannot create');
    const wrongPrompt = await prompt();
    assert.match(await wrongPrompt.textContent(), /passcode|correct/i);
    assert.equal(await wrongPrompt.locator('[id^="hp-passcode-"]').inputValue(), '', 'wrong proof cleared');
    await approve();
    await page.waitForFunction(() => !document.querySelector('[data-modal-id="__new_project"]'));
    assert.equal(accepted[0].id, 'human_create');
    assert.equal(accepted[0].body.project_path, '/smoke/create');
    assert.equal(accepted[0].body.name, 'human_create');
    assert.equal(accepted[0].body.status, 'active');
    console.log(`PASS Create cancel/wrong/correct with draft and original body @${width}`);

    // Same form supports automatic folder creation, and the existing first-use
    // passcode setup is reachable by a real human click on a fresh install.
    configured = false;
    await startCreate('human_auto', '');
    const setup = await prompt();
    await setup.locator('[id^="hp-new-"]').fill(PROOF);
    await setup.locator('.btn-add').click();
    await setup.locator('[id^="hp-passcode-"]').waitFor();
    await setup.locator('.btn-add').click();
    await setup.waitFor({ state: 'detached' });
    assert.equal(accepted.at(-1).body.project_path, '');
    console.log(`PASS Create blank workspace through first-use passcode setup @${width}`);

    await page.evaluate(() => window.openProjectProfileDialog('smoke_alpha'));
    const input = page.locator('#pfd-path');
    await input.fill('/smoke/typed');
    await input.press('Enter');
    const beforeEdit = calls.length;
    await cancel();
    assert.equal(calls.length, beforeEdit, 'cancel typed edit sends no write');
    assert.equal(await input.inputValue(), '/smoke/original', 'cancel typed edit restores saved folder');
    await input.fill('/smoke/typed');
    await input.press('Enter');
    await approve();
    await page.waitForFunction(() => allProjects.find(p => p.id === 'smoke_alpha')?.project_path === '/smoke/typed');
    assert.deepEqual(accepted.at(-1).body, { project_path: '/smoke/typed', passcode: PROOF });
    console.log(`PASS typed workspace Edit cancel/correct @${width}`);

    await page.evaluate(() => window.openFolderPicker('smoke_alpha', { startPath: '/smoke/picked' }));
    await page.waitForFunction(() => document.getElementById('fp-path-input')?.value === '/smoke/picked');
    await page.locator('#fp-select').click();
    const beforePicker = calls.length;
    await cancel();
    assert.equal(calls.length, beforePicker, 'cancel picker sends no write');
    assert.equal(await page.locator('#fp-path-input').inputValue(), '/smoke/picked', 'picker stays open on cancel');
    refusePath = '/smoke/picked';
    await page.locator('#fp-select').click();
    await approve();
    await page.waitForFunction(() => document.body.textContent.includes('Workspace test refusal'));
    assert.equal(await page.locator('#fp-overlay').count(), 1, 'rejected picker stays open');
    refusePath = '';
    await page.locator('#fp-select').click();
    await approve();
    await page.waitForFunction(() => !document.getElementById('fp-overlay'));
    assert.deepEqual(accepted.at(-1).body, { project_path: '/smoke/picked', passcode: PROOF });
    await page.evaluate(() => window.closeProfileDialog());
    console.log(`PASS folder-picker Edit cancel/refusal/correct with real prompt clicks @${width}`);

    await page.evaluate(() => window.openClaudeImport());
    await page.waitForSelector('[data-modal-id="__claude_import"] .ci-row');
    await page.locator('[data-modal-id="__claude_import"] .ci-actions button').click();
    const beforeImport = calls.length;
    await cancel();
    assert.equal(calls.length, beforeImport, 'cancel import sends no write');
    await page.waitForSelector('[data-modal-id="__claude_import"] .ci-row input:checked');
    await page.locator('[data-modal-id="__claude_import"] .ci-actions button').click();
    await approve();
    await cancel(); // cancel second project after first succeeds
    await page.waitForSelector('[data-modal-id="__claude_import"] .ci-results');
    assert.equal(accepted.at(-1).id, 'import_a');
    assert.equal(accepted.at(-1).body.project_path, '/smoke/import-a');
    assert.equal(await page.locator('[data-modal-id="__claude_import"] .ci-row').count(), 1);
    assert.equal(await page.locator('[data-modal-id="__claude_import"] .ci-row input:checked').count(), 1);
    console.log(`PASS Import cancel/partial-success preserves remaining selection @${width}`);
    assert.equal(alerts.length, 0, `unexpected alerts: ${alerts}`);
    assert.deepEqual(errors, [], 'no page errors');
  } finally { await ctx.close(); }
}

try { for (const width of [1440, 390]) await scenario(width); }
finally { await browser.close(); }
console.log('PASS MC-1080 routing human UI smoke');
