#!/usr/bin/env node
/**
 * Settings -> System -> "Scheduled backup" row (MC-983, BACKUP_EXPORT_SPEC.md
 * §7 Phase 4).
 *
 * WHY THIS EXISTS
 * ----------------
 * Pins the UI half of the scheduled auto-backup feature: the Off/Daily/Weekly
 * segmented control saves via PUT /api/config, the keep-count number input
 * saves the same way, and the status line reflects GET
 * /api/backup/schedule-status (off / last-run-and-next-run / a failed last
 * run's error) without a page reload.
 *
 * Hermetic, same idiom as settings-update-row.mjs: page + static assets
 * served from THIS checkout, config/status canned in-memory, everything else
 * aborted. No running MC server, no real backup ever runs.
 *
 * RUN: node tools/smoke/settings-backup-schedule.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let config = {};
let scheduleStatus = { cadence: 'off', keep: 3, last_run_at: null, last_status: null,
  last_error: null, next_run_at: null, overdue: false };
let configPuts = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
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
  if (path === '/api/config' && route.request().method() === 'GET') return json(config);
  if (path === '/api/config' && route.request().method() === 'PUT') {
    const body = JSON.parse(route.request().postData() || '{}');
    configPuts.push(body);
    Object.assign(config, body);
    if ('backup_schedule' in body) scheduleStatus.cadence = body.backup_schedule;
    if ('backup_keep' in body) scheduleStatus.keep = body.backup_keep;
    return json({ updated: Object.keys(body) });
  }
  if (path === '/api/backup/schedule-status') return json(scheduleStatus);
  if (path === '/api/system/update/status') return json({ is_git_repo: true, behind: 0, ahead: 0,
    has_local_changes: false, update_available: false, projects_in_install_dir: [] });
  return route.abort();
});

const openSystemSettings = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#mc-backup-schedule-seg', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('system'));
  await page.evaluate(() => {
    const secs = [...document.querySelectorAll(
      '#settings-detail .settings-detail-pane[data-cat="system"] .settings-section')];
    const idx = secs.findIndex(s =>
      (s.querySelector('.settings-section-title')?.textContent || '').trim() === 'Paths & Server');
    if (idx < 0) throw new Error('Paths & Server settings section not found');
    window.drillSettingsSub(idx);
  });
  await page.waitForFunction(
    () => !document.getElementById('mc-backup-schedule-seg')?.closest('.settings-detail-pane')
      ?.classList.contains('settings-hidden')
      && !document.getElementById('mc-backup-schedule-seg')?.closest('.settings-section')
      ?.classList.contains('settings-hidden'),
    { timeout: 5000 });
};

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });

  // ── 1. Off by default: status line says off, no active cadence button ──
  await openSystemSettings();
  await page.waitForFunction(
    () => (document.getElementById('backup-schedule-status-hint')?.textContent || '').includes('off'),
    { timeout: 5000 });
  ok('default: status hint reports scheduled backup is off');
  const activeLabel = await page.evaluate(() =>
    document.querySelector('#mc-backup-schedule-seg button.active')?.textContent?.trim());
  activeLabel === 'Off'
    ? ok('default: "Off" segment is the active one')
    : fail(`expected "Off" active, got "${activeLabel}"`);

  // ── 2. Clicking Daily PUTs config and flips the active segment ─────────
  configPuts = [];
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('#mc-backup-schedule-seg button')];
    btns.find(b => b.textContent.trim() === 'Daily').click();
  });
  await page.waitForFunction(
    () => document.querySelector('#mc-backup-schedule-seg button.active')?.textContent?.trim() === 'Daily',
    { timeout: 5000 });
  (configPuts.length === 1 && configPuts[0].backup_schedule === 'daily')
    ? ok('clicking Daily PUTs /api/config {backup_schedule:"daily"}')
    : fail(`unexpected config PUT(s): ${JSON.stringify(configPuts)}`);
  ok('active segment moved to Daily immediately, no reload');

  // ── 3. Keep-count input saves via saveSetting ───────────────────────────
  configPuts = [];
  await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.settings-row')];
    const row = rows.find(r => r.querySelector('.settings-label')?.textContent?.trim() === 'Keep');
    const input = row.querySelector('input');
    input.value = '7';
    input.dispatchEvent(new Event('change'));
  });
  await page.waitForFunction(() => true, { timeout: 100 }).catch(() => {});
  (configPuts.length === 1 && configPuts[0].backup_keep === 7)
    ? ok('changing Keep PUTs /api/config {backup_keep:7}')
    : fail(`unexpected config PUT(s) for keep: ${JSON.stringify(configPuts)}`);

  // ── 4. Status line reflects a real last/next run ────────────────────────
  scheduleStatus = { cadence: 'daily', keep: 7, last_run_at: '2026-09-25T08:00:00Z',
    last_status: 'success', last_error: null, next_run_at: '2026-09-26T08:00:00Z', overdue: false };
  await page.evaluate(() => window.refreshBackupScheduleSection());
  await page.waitForFunction(
    () => (document.getElementById('backup-schedule-status-hint')?.textContent || '').includes('Next'),
    { timeout: 5000 });
  const successHint = await page.textContent('#backup-schedule-status-hint');
  /Last:.*Next:/.test(successHint)
    ? ok(`status hint shows last + next run: "${successHint.trim()}"`)
    : fail(`status hint missing last/next: "${successHint}"`);

  // ── 5. A failed last run surfaces its error, not a silent timestamp ────
  scheduleStatus = { cadence: 'daily', keep: 7, last_run_at: '2026-09-25T08:00:00Z',
    last_status: 'error', last_error: 'disk full', next_run_at: null, overdue: true };
  await page.evaluate(() => window.refreshBackupScheduleSection());
  await page.waitForFunction(
    () => /disk full/.test(document.getElementById('backup-schedule-status-hint')?.textContent || ''),
    { timeout: 5000 });
  const failHint = await page.textContent('#backup-schedule-status-hint');
  /due now/.test(failHint)
    ? ok(`status hint shows "due now" when overdue: "${failHint.trim()}"`)
    : fail(`status hint missing overdue "due now": "${failHint}"`);

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
