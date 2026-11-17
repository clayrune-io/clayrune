// MC-1182: real Skills handlers + shared passcode modal, hermetic wire assertions.
// No live server, installed skill, git clone, or real passcode is used.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const origin = 'http://mc.smoke.test';
const calls = [];
let response = { name: 'smoke', action: 'archived' };
let responseStatus = 200;
let configured = true;
let checks = 0;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', d => d.accept());
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.route('**/*', route => {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
  if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
  if (path === '/api/projects') return json([]);
  if (path === '/api/config') return json({});
  if (path === '/api/local-auth/status') return json({ configured });
  if (path === '/api/local-auth/set') { configured = true; return json({ ok: true }); }
  if (path.startsWith('/api/skills') && req.method() !== 'GET') {
    const data = req.postDataJSON();
    calls.push({ path, method: req.method(), data });
    if (data.passcode !== 'smoke-passcode') return json({ error: 'bad_passcode' }, 403);
    return json(response, responseStatus);
  }
  if (path === '/api/skills') return json([]);
  if (path === '/api/skills/usage') return json({});
  if (path.startsWith('/api/skills/')) return json({ name: 'smoke', description: 'Smoke procedure', body: 'Verify concrete results.' });
  return route.abort();
});

async function clear() {
  // Let the real handlers' delayed close finish before reopening their modal.
  await page.waitForTimeout(850);
  await page.evaluate(() => document.querySelectorAll('[data-modal-id]').forEach(el => window.closeModalById(el.dataset.modalId)));
  response = { name: 'smoke', action: 'archived' };
  responseStatus = 200;
}

async function begin(code) {
  await page.evaluate(code => {
    window.skillsActionDone = false;
    window.skillsActionError = null;
    Promise.resolve((0, eval)(code)).then(() => { window.skillsActionDone = true; }, e => {
      window.skillsActionError = e.message; window.skillsActionDone = true;
    });
  }, code);
  await page.locator('[id^="hp-passcode-"]').waitFor();
}

async function submit(path, method, { wrong = false, force = false, newlySet = false } = {}) {
  const before = calls.length;
  const input = page.locator('[id^="hp-passcode-"]');
  assert.equal(await input.inputValue(), newlySet ? 'smoke-passcode' : '');
  if (wrong) {
    await input.fill('wrong');
    await input.press('Enter');
    await page.getByText('Wrong dashboard passcode.', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), '');
  }
  await input.fill('smoke-passcode');
  await input.press('Enter');
  await page.waitForFunction(() => window.skillsActionDone);
  assert.equal(await page.evaluate(() => window.skillsActionError), null);
  const last = calls.at(-1);
  assert.equal(last.path, path);
  assert.equal(last.method, method);
  assert.equal(last.data.passcode, 'smoke-passcode');
  if (force) assert.equal(last.data.force, true);
  assert.equal(calls.length - before, wrong ? 2 : 1);
  console.log(`PASS ${method} ${path}${force ? ' force retry' : ''}`);
  checks++;
}

async function editor(isNew) {
  await page.evaluate(isNew => window.openSkillEditor('global', isNew ? '' : 'smoke', null, isNew), isNew);
  await page.locator('#se-description').fill('Smoke procedure');
  if (isNew) await page.locator('#se-name').fill('smoke');
  await page.locator('#se-body').fill('Verify concrete results.');
  await begin(`window.saveSkillFromEditor('${isNew ? '__skill_new' : '__skill_global_smoke'}', ${isNew}, 'global', 'smoke', null)`);
}

async function gitPicker(plugin = false) {
  await page.evaluate(() => window.openSkillImportGit());
  await page.locator('#sig-url').fill('https://example.test/skills.git');
  response = { staging_id: 'deadbeef1234', candidates: [{ name: 'smoke', rel_dir: 'skills/smoke' }] };
  if (plugin) response.plugin = { name: 'smoke-plugin', skill_count: 1, command_count: 1, agent_count: 1 };
  await begin("window._doSkillImportGit('__skill_import_git')");
  await submit('/api/skills/import/git', 'POST');
  response = { name: 'smoke', message: 'Installed' };
}

try {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSkillEditor === 'function' && typeof window.humanProofFetch === 'function');
  await editor(true);
  await submit('/api/skills', 'POST', { wrong: true });
  await clear();
  await editor(false);
  await submit('/api/skills/global/smoke', 'PUT');
  await clear();
  for (const [code, path, method] of [
    ["window.archiveSkillAction('global','smoke',null)", '/api/skills/global/smoke', 'DELETE'],
    ["window.deleteSkillAction('archive','smoke',null,true)", '/api/skills/archive/smoke', 'DELETE'],
    ["window.restoreSkillAction('smoke')", '/api/skills/archive/smoke/restore', 'POST'],
  ]) { await begin(code); await submit(path, method); await clear(); }

  await page.evaluate(() => window.openSkillImportPaste());
  await page.locator('#sip-content').fill('---\nname: smoke\ndescription: Smoke procedure\n---\nVerify results.');
  responseStatus = 422;
  response = { quarantined: true, tier: 'community', findings: [], quarantine_id: 'abc123', message: 'Review findings' };
  await begin("window._doSkillImportPaste('__skill_import_paste')");
  await submit('/api/skills/import/paste', 'POST');
  await page.locator('#si-quarantine-banner').waitFor();
  responseStatus = 200;
  response = { name: 'smoke' };
  await begin("window._doSkillImportPaste('__skill_import_paste',true)");
  await submit('/api/skills/import/paste', 'POST', { force: true });
  await clear();

  await page.evaluate(() => window.openSkillImportFolder());
  await page.locator('#sif-path').fill('C:/smoke/skill');
  await begin("window._doSkillImportFolder('__skill_import_folder',null,true)");
  await submit('/api/skills/import/folder', 'POST', { force: true });
  await clear();

  await gitPicker();
  await begin("window._doSkillImportGitInstallOne('__skill_import_git','skills/smoke','smoke',true)");
  await submit('/api/skills/import/git/install', 'POST', { force: true });
  await clear();

  await gitPicker(true);
  await begin("window._doSkillImportFullPlugin('__skill_import_git',true)");
  await submit('/api/skills/import/plugin', 'POST', { force: true });
  await clear();

  await gitPicker();
  await begin("window._doSkillImportGitCancel('__skill_import_git')");
  const beforeCancel = calls.length;
  await page.getByRole('button', { name: 'Cancel', exact: true }).last().click();
  await page.waitForFunction(() => window.skillsActionDone);
  assert.equal(calls.length, beforeCancel);
  assert.equal(await page.locator('[data-modal-id="__skill_import_git"]').count(), 1);
  checks++; console.log('PASS cancelling passcode keeps staging picker and sends no request');
  await begin("window._doSkillImportGitCancel('__skill_import_git')");
  await submit('/api/skills/import/git/cancel', 'POST');
  await clear();

  await page.evaluate(() => window.openSkillImportBrowse());
  await begin("window._sibInstallHere('__skill_import_browse','global','smoke',null)");
  await submit('/api/skills', 'POST');
  await clear();

  configured = false;
  await page.evaluate(() => { window.skillsActionDone = false; window.restoreSkillAction('smoke').then(() => { window.skillsActionDone = true; }); });
  await page.locator('[id^="hp-new-"]').fill('smoke-passcode');
  await page.locator('[id^="hp-new-"]').press('Enter');
  await page.locator('[id^="hp-passcode-"]').waitFor();
  await submit('/api/skills/archive/smoke/restore', 'POST', { newlySet: true });
  assert.equal(configured, true);
  checks++; console.log('PASS unconfigured dashboard uses existing set-passcode flow');
  assert.deepEqual(errors, []);
  checks++; console.log('PASS no browser runtime errors');
  console.log(`${checks} passed, 0 failed`);
} finally {
  await browser.close();
}
