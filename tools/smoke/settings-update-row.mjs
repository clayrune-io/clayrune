#!/usr/bin/env node
/**
 * Settings -> Server -> "Update Clayrune" row.
 *
 * WHY THIS EXISTS
 * ----------------
 * Real case, 2026-09-14 (Amit): a dirty working tree left this row stuck on
 * "Blocked: Local changes... Stash or commit first." with NO path forward for
 * someone who doesn't use git — updates must never be blocked for a
 * non-developer. The fix adds a self-service "Set aside local changes and
 * update" button (POST /api/system/update {stash:true}) plus a warning when a
 * project's workspace points at the install directory itself (the root cause
 * of Amit's case — see mc/blueprints/project_routes.py's
 * _refuse_project_path_in_install_dir).
 *
 * This smoke pins the UI side of that fix:
 *   1. A clean, up-to-date tree shows the normal row, no stash button.
 *   2. A dirty tree shows Blocked + the stash button (previously the ONLY
 *      option was a disabled button and prose telling the user to run git).
 *   3. Clicking the stash button posts {stash:true} and renders the returned
 *      stash message so the user knows their work isn't gone.
 *   4. A project pointed at the install dir surfaces the warning banner by
 *      name; it's silent when there's nothing to warn about.
 *
 * Hermetic, like boot-smoke.mjs / backup-panel-breakdown.mjs: page + static
 * assets served from THIS checkout, /api/system/update* canned, everything
 * else aborted. No running MC server, no real git operation.
 *
 * RUN: node tools/smoke/settings-update-row.mjs
 * Screenshot of the Blocked state: set MC_SMOKE_SHOT=<path>.
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

const CLEAN_STATUS = {
  is_git_repo: true, install_dir: 'C:\\Clayrune', branch: 'master',
  commit: 'abc1234', commit_date: '2026-09-14', version: 'v2.3.0',
  remote_commit: 'abc1234', remote_commit_date: '2026-09-14', remote_version: 'v2.3.0',
  behind: 0, ahead: 0, has_local_changes: false, update_available: false,
  projects_in_install_dir: [],
};

const DIRTY_STATUS = {
  ...CLEAN_STATUS,
  behind: 3, update_available: false, has_local_changes: true,
  projects_in_install_dir: [{ id: 'amit_proj', name: "Amit's Clayrune" }],
};

let statusBody = CLEAN_STATUS;
let updateCalls = [];
let updateResponse = { ok: true, new_commit: 'def5678', previous_commit: 'abc1234',
  resynced: false, recent_log: '', restart_recommended: true,
  stashed: 'clayrune-auto-stash 2026-09-14T00:00:00Z abc1234' };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
page.on('dialog', (d) => d.accept());  // confirm() before stash+update
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const url = new URL(route.request().url());
  const path = url.pathname;
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });

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
  if (path === '/api/config') return json({});
  if (path === '/api/system/update/status') return json(statusBody);
  if (path === '/api/system/update') {
    updateCalls.push(JSON.parse(route.request().postData() || '{}'));
    return json(updateResponse);
  }
  return route.abort();
});

const openServerSettings = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  // Present as soon as _renderSettings() builds ALL category panes in one
  // shot — visibility toggles only after drilling, so wait for ATTACHED here.
  await page.waitForSelector('#update-status-hint', { state: 'attached', timeout: 10000 });
  // 'system' has multiple sections (Paths & Server / Advanced features /
  // Server / Help) -> drillSettings lands on the layer-2 sub-list, not the
  // row itself. Find "Server"'s index by its own title text (not a hardcoded
  // index — section order is UI content, not a contract) and drill into it.
  await page.evaluate(() => window.drillSettings('system'));
  await page.evaluate(() => {
    const secs = [...document.querySelectorAll(
      '#settings-detail .settings-detail-pane[data-cat="system"] .settings-section')];
    const idx = secs.findIndex(s =>
      (s.querySelector('.settings-section-title')?.textContent || '').trim() === 'Server');
    if (idx < 0) throw new Error('Server settings section not found');
    window.drillSettingsSub(idx);
  });
  await page.waitForFunction(
    () => !document.getElementById('update-status-hint')?.closest('.settings-detail-pane')
      ?.classList.contains('settings-hidden')
      && !document.getElementById('update-status-hint')?.closest('.settings-section')
      ?.classList.contains('settings-hidden'),
    { timeout: 5000 });
};

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });

  // ── 1. Clean tree: normal row, no stash button ──────────────────────────
  statusBody = CLEAN_STATUS;
  await openServerSettings();
  await page.waitForFunction(
    () => (document.getElementById('update-status-hint')?.textContent || '').includes('Up to date'),
    { timeout: 5000 });
  const cleanStashVisible = await page.evaluate(() =>
    getComputedStyle(document.getElementById('update-stash-btn')).display !== 'none');
  !cleanStashVisible
    ? ok('clean/behind tree: no stash button shown')
    : fail('stash button shown when tree is clean');
  const cleanWarningVisible = await page.evaluate(() =>
    getComputedStyle(document.getElementById('update-install-dir-warning')).display !== 'none');
  !cleanWarningVisible
    ? ok('no projects in install dir: warning banner hidden')
    : fail('warning banner shown with nothing to warn about');

  // ── 2. Dirty tree: Blocked + stash button + install-dir warning ─────────
  statusBody = DIRTY_STATUS;
  await openServerSettings();
  await page.waitForFunction(
    () => (document.getElementById('update-btn')?.textContent || '') === 'Blocked',
    { timeout: 5000 });
  ok('dirty tree: plain Update button reads Blocked');
  const stashVisible = await page.evaluate(() =>
    getComputedStyle(document.getElementById('update-stash-btn')).display !== 'none');
  stashVisible
    ? ok('dirty tree: "Set aside local changes and update" button is shown')
    : fail('stash button not shown on a dirty tree — no self-service path for a non-developer');
  const warningText = await page.textContent('#update-install-dir-warning');
  /Amit's Clayrune/.test(warningText)
    ? ok(`install-dir warning names the offending project: "${warningText.trim().slice(0, 80)}..."`)
    : fail(`install-dir warning missing/wrong: "${warningText}"`);

  if (process.env.MC_SMOKE_SHOT) {
    await page.screenshot({ path: process.env.MC_SMOKE_SHOT });
    ok('screenshot of the Blocked state written to ' + process.env.MC_SMOKE_SHOT);
  }

  // ── 3. Clicking stash posts {stash:true} and shows the returned message ─
  updateCalls = [];
  await page.click('#update-stash-btn');
  await page.waitForFunction(
    () => /Updated to/.test(document.getElementById('update-status-hint')?.textContent || ''),
    { timeout: 5000 });
  updateCalls.length === 1 && updateCalls[0].stash === true
    ? ok('stash button POSTs /api/system/update with {stash:true}')
    : fail(`unexpected update call(s): ${JSON.stringify(updateCalls)}`);
  const afterHint = await page.textContent('#update-status-hint');
  afterHint.includes('clayrune-auto-stash')
    ? ok('the stash message is shown so the user knows how to recover their work')
    : fail(`stash message not surfaced: "${afterHint}"`);
  const stashBtnHiddenAfter = await page.evaluate(() =>
    getComputedStyle(document.getElementById('update-stash-btn')).display === 'none');
  stashBtnHiddenAfter
    ? ok('stash button hides itself after a successful update')
    : fail('stash button still showing after update succeeded');

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
