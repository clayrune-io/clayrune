/** Hermetic Security setting click-through against this checkout's assets. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const assets = loadStaticJsCss(root);
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    let config = { vault_idle_lock_minutes: 73 };
    const puts = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
    await page.route('**/*', route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(root + 'static/index.html', 'utf8') });
      if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
      if (path === '/api/projects') return json([]);
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/agent/providers') return json({ providers: [], default: 'claude' });
      if (path === '/api/config') {
        if (request.method() === 'PUT') {
          const body = request.postDataJSON();
          puts.push(body);
          const { passcode, ...settings } = body;
          config = { ...config, ...settings };
          return json({ ok: true });
        }
        return json(config);
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForFunction(() => typeof window.openSettings === 'function');
    const open = async () => {
      await page.evaluate(() => window.closeModalById('__settings'));
      // Closing on mobile unwinds history asynchronously; let popstate finish.
      await page.waitForTimeout(250);
      await page.evaluate(() => window.openSettings());
      await page.waitForSelector('[onclick*="vault_keep_unlocked_on_restart"]', { state: 'attached' });
      await page.evaluate(() => {
        window.drillSettings('system');
        const sections = [...document.querySelectorAll('[data-cat="system"] .settings-section')];
        const index = sections.findIndex(section => section.querySelector('.settings-section-title')?.textContent.trim() === 'Security');
        if (index < 0) throw new Error('Security missing');
        window.drillSettingsSub(index);
      });
    };
    await open();
    const toggle = page.locator('[onclick*="vault_keep_unlocked_on_restart"]');
    await toggle.waitFor({ state: 'visible' });
    assert.match(await toggle.getAttribute('class'), /\bon\b/);
    for (const value of [false, true]) {
      await toggle.click();
      await page.waitForSelector('[data-modal-id^="__human-proof-"]');
      await page.evaluate(() => {
        const modal = document.querySelector('[data-modal-id^="__human-proof-"]');
        const id = modal.dataset.modalId;
        document.getElementById(`hp-passcode-${id}`).value = 'synthetic-smoke-passcode';
        window._hpSubmit(id);
      });
      await page.waitForFunction(value => document.querySelector('[onclick*="vault_keep_unlocked_on_restart"]').classList.contains('on') === value, value);
      assert.equal(config.vault_keep_unlocked_on_restart, value);
      assert.equal(config.vault_idle_lock_minutes, 73);
      assert.deepEqual(Object.keys(puts.at(-1)).sort(), ['passcode', 'vault_keep_unlocked_on_restart']);
      await open();
      assert.equal((await toggle.getAttribute('class')).split(' ').includes('on'), value);
    }
    assert.deepEqual(errors, []);
    console.log(`PASS ${width}px: default on, save off/on, reload, idle unchanged, passcode retained`);
    await context.close();
  }
} finally {
  await browser.close();
}
