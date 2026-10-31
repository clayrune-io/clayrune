// Hermetic Settings component: actual module/CSS, no live server or credentials.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const root = fileURLToPath(new URL('../../', import.meta.url));
const moduleText = readFileSync(root + 'static/js/unattended-retry-settings.js', 'utf8');
const css = readFileSync(root + 'static/css/app.css', 'utf8');
const browser = await chromium.launch({headless: true});
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({viewport: {width, height: 900}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<div id="host" style="max-width:480px;padding:16px;box-sizing:border-box"></div>');
    await page.addStyleTag({content: css});
    await page.evaluate(() => {
      window.API_BASE = '';
      window.esc = value => String(value).replace(/[&<>"']/g, c => ({
        '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
      }[c]));
      window.calls = [];
      window.outcome = {ok: true, body: {ok: true}};
      window.humanProofFetch = async (...args) => { window.calls.push(args); return window.outcome; };
    });
    await page.addScriptTag({type: 'module', content: moduleText});
    await page.waitForFunction(() => !!window.UnattendedRetrySettings);
    await page.evaluate(() => {
      const names = ['claude', 'qwen', 'codex', 'gemini', 'opencode', 'goose', 'aider', 'kiro'];
      document.getElementById('host').innerHTML = window.UnattendedRetrySettings.render({
        unattended_retry: {max_wait: 30, max_usage_limit_wait: 120, usage_limit_recheck: 5},
        unattended_retry_capabilities: names.map(engine => ({engine,
          status: engine === 'claude' ? 'NATIVE' : engine === 'qwen' ? 'PARTIAL' : 'UNSUPPORTED',
          detail: 'Audited support label'})),
      }, {toggle: () => '<button>Off</button>'});
    });
    await page.locator('#retry-max_wait').fill('45');
    await page.locator('#retry-max_wait').dispatchEvent('change');
    await page.waitForFunction(() => document.getElementById('retry-save-status').textContent.startsWith('Saved.'));
    const calls = await page.evaluate(() => window.calls);
    assert.equal(calls.length, 1);
    assert.deepEqual(JSON.parse(calls[0][1].body), {unattended_retry: {
      max_wait: 45, max_usage_limit_wait: 120, usage_limit_recheck: 5}});
    assert.equal(calls[0][2].title, 'Save retry budgets');
    assert.match(await page.locator('#host').innerText(), /codex: UNSUPPORTED/);
    assert.match(await page.locator('#host').innerText(), /qwen: PARTIAL/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `Settings must fit the ${width}px viewport`);
    await page.evaluate(() => { window.outcome = null; });
    await page.locator('#retry-max_wait').fill('50');
    await page.locator('#retry-max_wait').dispatchEvent('change');
    await page.waitForFunction(() => document.getElementById('retry-save-status').textContent === 'Save cancelled.');
    assert.equal(await page.locator('#retry-max_wait').inputValue(), '45');
    await page.locator('#retry-max_wait').fill('0');
    await page.locator('#retry-max_wait').dispatchEvent('change');
    assert.equal(await page.evaluate(() => window.calls.length), 2);
    assert.deepEqual(errors, []);
    await page.close();
    console.log(`PASS retry Settings at ${width}px: full-block save, human gate, cancellation, validation, labels`);
  }
} finally { await browser.close(); }
