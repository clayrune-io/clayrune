#!/usr/bin/env node
/** Real-server default routing. Requires an isolated server, never ports 5199/5200.
 * MC_SMOKE_BASE_URL=http://127.0.0.1:<test-port> node tools/smoke/desk-v1-default.mjs
 * Completes onboarding on disposable MC_DATA_DIR. No config mocks, Desk-setting
 * writes, sign-ins or publishing.
 */
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = new URL(process.env.MC_SMOKE_BASE_URL || 'http://invalid/');
assert(['127.0.0.1', 'localhost'].includes(base.hostname)
  && base.port && !['5199', '5200'].includes(base.port),
'MC_SMOKE_BASE_URL must name an isolated loopback test port, never 5199/5200');
assert(process.env.MC_DATA_DIR, 'MC_DATA_DIR must name the disposable server data directory');

let passed = 0;
const check = (condition, message) => {
  assert(condition, message);
  console.log('  PASS ' + message);
  passed++;
};
const browser = await chromium.launch({ headless: true });
const shots = new URL('../../_scratch/desk_v1_default/', import.meta.url);
mkdirSync(shots, { recursive: true });
try {
  const response = await fetch(new URL('/api/config', base));
  assert(response.ok, 'live config API responds');
  const config = await response.json();
  check(config.desk_v1 === true && config.desk_v1_live === true, 'server supplies live Desk defaults');
  const saved = JSON.parse(readFileSync(resolve(process.env.MC_DATA_DIR, 'config.json'), 'utf8'));
  check(saved.desk_v1 === true && saved.desk_v1_default_migrated === true,
    'isolated config durably records the default migration');
  // Fresh-install setup deliberately minimizes other panels. Finish only that
  // wizard through its real endpoint before exercising normal Desk/Settings UI.
  const setup = await fetch(new URL('/api/setup/complete', base), { method: 'POST' });
  check(setup.ok, 'disposable server completes onboarding before UI checks');
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base.href, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => typeof window.openDesk === 'function'
        && typeof _globalConfig !== 'undefined' && _globalConfig.desk_v1 === true);
      const workspace = page.waitForResponse(r => new URL(r.url()).pathname === '/api/desk/workspace');
      await page.evaluate(() => window.openDesk());
      const live = await workspace;
      await page.locator('.desk-v1-shell .desk-v1-home').waitFor();
      check(live.ok(), `[${width}] default Desk reads the live workspace`);
      const shell = await page.locator('.desk-v1-shell').innerText();
      check(!shell.includes('Demo data - not your workspace'), `[${width}] default Desk has no demo banner`);
      await page.evaluate(async () => {
        await window.openSettings();
        window.drillSettings('system');
        const sections = Array.from(document.querySelectorAll('[data-cat="system"] > .settings-section'));
        const titles = sections.map(s => s.querySelector('.settings-section-title')?.textContent);
        const index = titles.findIndex(t => t?.trim() === 'Runtime and availability');
        if (index < 0) throw new Error('Settings has no Runtime and availability section');
        window.drillSettingsSub(index);
      });
      const row = page.locator('[data-cat="system"] .settings-row')
        .filter({ has: page.getByText('Desk v1', { exact: true }) });
      await row.waitFor();
      check(await row.locator('.settings-hint').innerText()
        === 'Manage campaigns, content and publishing in the live Desk workspace. The legacy Desk stays read-only while this is on.',
      `[${width}] Settings describes the live workspace and legacy read-only behavior`);
      await page.screenshot({ path: fileURLToPath(new URL(`settings-${width}.png`, shots)), animations: 'disabled' });
      check(errors.length === 0, `[${width}] real page opens without uncaught errors: ${errors.join('; ')}`);
    } finally {
      await context.close();
    }
  }
  console.log(`${passed} passed`);
} finally {
  await browser.close();
}
