// Hermetic real Settings shell, desktop/mobile, with every request intercepted.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const files = loadStaticJsCss(root);
const PATH = '/api/agent-mail/deployment';
const ROOT = 'agent_mail_root_public_key', ORIGIN = 'agent_mail_service_origin';
const key = Buffer.from(Array.from({ length: 32 }, (_, i) => i)).toString('base64url');
const pass = 'test-only-human-proof';
const browser = await chromium.launch();
let checks = 0;
const check = (condition, name) => { assert.ok(condition, name); checks++; console.log('PASS ' + name); };
try {
  for (const width of [1000, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 850 } });
    const page = await ctx.newPage();
    const errors = [], writes = [];
    let readCount = 0, failRead = false, rejectWrite = false;
    let saved = { [ROOT]: '', [ORIGIN]: '', set: { [ROOT]: false, [ORIGIN]: false } };
    page.on('pageerror', (e) => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
    await page.route('**/*', (route) => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === PATH && req.method() === 'GET') { readCount++; return json(saved, failRead ? 503 : 200); }
      if (path === PATH && req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push(body);
        if (body.passcode !== pass) return json({ error: 'bad_passcode' }, 403);
        if (rejectWrite) return json({ error: 'invalid_service_origin' }, 400);
        saved = { [ROOT]: body[ROOT], [ORIGIN]: body[ORIGIN], set: { [ROOT]: true, [ORIGIN]: true } };
        return json({ ok: true, ...saved });
      }
      if (req.method() !== 'GET') throw new Error('Unexpected write: ' + path);
      if (path === '/api/projects') return json([]);
      if (path === '/api/config') return json({ setup_completed: true });
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/agent-mail/enrollment') return json({ error: 'not_found' }, 404);
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.agentMailDeploymentSettingsHTML);
    async function open() {
      await page.evaluate(() => { window.closeModalById('__settings'); window.openSettings(); });
      await page.locator('#am-deployment-root').waitFor({ state: 'attached' });
      await page.evaluate(() => {
        window.drillSettings('connect');
        const sections = [...document.querySelectorAll('.settings-detail-pane[data-cat="connect"] .settings-section')];
        window.drillSettingsSub(sections.findIndex((s) => s.id === 'agent-mail-deployment-section'));
      });
      await page.locator('#am-deployment-root').waitFor({ state: 'visible' });
    }
    async function submit() {
      await page.locator('[data-am-deployment-form] button').click();
      await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
    }
    async function confirm() {
      await page.locator('input[id^="hp-passcode-"]').fill(pass);
      await page.locator('[data-modal-id^="__human-proof-"] .btn-add').click();
    }
    await open();
    check(await page.locator('[data-am-deployment-root-state]').textContent() === 'Not set', `${width}: empty trust state visible with mail disabled`);
    await page.locator('#am-deployment-root').fill(key);
    await page.locator('#am-deployment-origin').fill('https://mail.example.com');
    await submit();
    check(writes.length === 0, `${width}: no write before human proof`);
    await page.locator('[data-modal-id^="__human-proof-"] .btn-secondary').click();
    await page.getByText('Cancelled. Nothing saved.').waitFor();
    check(writes.length === 0, `${width}: cancellation writes nothing`);
    const readsBefore = readCount;
    await submit(); await confirm();
    await page.getByText('Deployment settings saved.').waitFor();
    check(writes.length === 1 && writes[0][ROOT] === key && writes[0][ORIGIN] === 'https://mail.example.com'
      && writes[0].passcode === pass && Object.keys(writes[0]).length === 3, `${width}: one exact human-gated PUT`);
    check(readCount > readsBefore && await page.locator('#am-deployment-root').inputValue() === key
      && await page.locator('[data-am-deployment-origin-state]').textContent() === 'Set', `${width}: saved readback rendered`);
    rejectWrite = true;
    await page.locator('#am-deployment-origin').fill('https://mail.example.com/');
    await submit(); await confirm();
    await page.getByText('Use an HTTPS hostname with no path, port, query or trailing slash.').waitFor();
    check(saved[ORIGIN] === 'https://mail.example.com', `${width}: validation refusal visible, saved origin preserved`);
    const layout = await page.locator('#agent-mail-deployment-section').evaluate((el) => ({
      over: el.scrollWidth - el.clientWidth, page: document.documentElement.scrollWidth - innerWidth,
    }));
    check(layout.over <= 1 && layout.page <= 1, `${width}: no horizontal overflow`);
    check(await page.evaluate((p) => !document.body.textContent.includes(p)
      && !JSON.stringify(localStorage).includes(p) && !JSON.stringify(sessionStorage).includes(p), pass), `${width}: passcode cleared and not stored`);
    failRead = true;
    await page.evaluate(() => window.refreshAgentMailDeployment());
    await page.getByText('Could not read deployment settings. Reopen Settings to retry.').waitFor();
    check(true, `${width}: read failure visible`);
    check(errors.length === 0, `${width}: no page errors (${errors.join(', ')})`);
    await ctx.close();
  }
} finally { await browser.close(); }
console.log(`${checks} checks passed`);
