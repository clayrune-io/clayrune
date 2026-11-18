// MC-1179 SK4 (failure 2): the skill editor opens with an empty Description and Body and an
// enabled Save, then fills them from a GET whose failure was thrown away. If the GET fails the
// person sees an existing skill as blank; typing a description and pressing Save sent
// body: '' and replaced the playbook with nothing.
//
// Hermetic: no server, no real skill, no passcode (humanProofFetch is replaced by a recorder).
// The other half of SK4 (frontmatter kept on Save) is server-side: tests/test_skill_editor_keeps_frontmatter.py.
//
// RUN   cd tools/smoke && node skills-editor-load-failure.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const origin = 'http://mc.smoke.test';
let getMode = 'ok';          // ok | 500 | 404 | network | slow
let releaseSlow = null;
let checks = 0;
const SAVE = 'button[onclick^="saveSkillFromEditor"]';   // not #se-save: that id is part of the fix
const pass = (m) => { checks++; console.log('PASS ' + m); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.route('**/*', async (route) => {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
  if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
  if (path === '/api/projects') return json([]);
  if (path === '/api/config') return json({});
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/skills') return json([]);
  if (path === '/api/skills/usage') return json({});
  if (path === '/api/skills/global/playbook' && req.method() === 'GET') {
    if (getMode === '500') return json({ error: 'boom' }, 500);
    if (getMode === '404') return json({ error: 'skill not found' }, 404);
    if (getMode === 'network') return route.abort();
    if (getMode === 'slow') await new Promise((r) => { releaseSlow = r; });
    return json({ name: 'playbook', description: 'Real description', body: 'Step 1. Step 2.' });
  }
  return route.abort();
});

async function openEditor() {
  await page.evaluate(() => {
    document.querySelectorAll('[data-modal-id]').forEach((el) => window.closeModalById(el.dataset.modalId));
    window.__puts = [];
    window.humanProofFetch = async (url, init) => { window.__puts.push({ url, method: init.method, body: JSON.parse(init.body) }); return { ok: true, status: 200, body: {} }; };
    window.openSkillEditor('global', 'playbook', null, false);
  });
  await page.locator(SAVE).waitFor();
}
const puts = () => page.evaluate(() => window.__puts);

try {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSkillEditor === 'function' && typeof window.humanProofFetch === 'function');

  for (const mode of ['500', '404', 'network']) {
    getMode = mode;
    await openEditor();
    await page.locator('#se-status').filter({ hasText: 'Could not load this skill' }).waitFor();
    assert.equal(await page.locator(SAVE).isDisabled(), true);
    pass(`GET ${mode}: the editor says it could not load and Save is disabled`);
    await page.locator('#se-description').fill('Typed over a blank editor');
    // A click on a disabled button does nothing; call the handler too, as an inline onclick would.
    await page.locator(SAVE).click({ force: true, timeout: 1000 }).catch(() => {});
    await page.evaluate(() => window.saveSkillFromEditor('__skill_global_playbook', false, 'global', 'playbook', null));
    assert.deepEqual(await puts(), []);
    pass(`GET ${mode}: Save sends no PUT, so the playbook is not replaced by an empty body`);
  }

  getMode = 'slow';
  await openEditor();
  assert.equal(await page.locator(SAVE).isDisabled(), true);
  await page.evaluate(() => window.saveSkillFromEditor('__skill_global_playbook', false, 'global', 'playbook', null));
  assert.deepEqual(await puts(), []);
  pass('while the skill is still loading Save is disabled and sends nothing');
  releaseSlow();
  await page.waitForFunction(() => !document.querySelector('button[onclick^="saveSkillFromEditor"]').disabled);
  pass('Save enables once the skill has loaded');

  getMode = 'ok';
  await openEditor();
  await page.waitForFunction(() => !document.querySelector('button[onclick^="saveSkillFromEditor"]').disabled);
  assert.equal(await page.locator('#se-body').inputValue(), 'Step 1. Step 2.');
  await page.locator('#se-description').fill('Real description, typo fixed');
  await page.locator(SAVE).click();
  await page.waitForFunction(() => window.__puts.length === 1);
  const [put] = await puts();
  assert.equal(put.method, 'PUT');
  assert.equal(put.url, '/api/skills/global/playbook');
  assert.deepEqual(put.body, { description: 'Real description, typo fixed', body: 'Step 1. Step 2.' });
  pass('a loaded skill saves its own body and the edited description');

  assert.deepEqual(errors, []);
  pass('no browser runtime errors');
  console.log(`${checks} passed, 0 failed`);
} finally {
  await browser.close();
}
