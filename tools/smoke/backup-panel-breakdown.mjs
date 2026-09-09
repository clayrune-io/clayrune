#!/usr/bin/env node
/**
 * Backup panel — the four defects Ron reported against the shipped panel.
 *
 * WHY THIS EXISTS
 * ---------------
 * 1. "Unprotected work files" auto-expanded its per-directory list with no way
 *    to close it — dozens of rows on a real install, pushing Create off-screen.
 * 2. Only 'unprotected' had a breakdown at all; the other four categories
 *    showed a total with no way to see what was in it.
 * 3. The destination was a bare text field — no picker, though Clayrune already
 *    ships a server-side folder picker that works over remote access.
 * 4. Create was synchronous: a ~48GB archive parked on a dead "Creating…"
 *    button for minutes with no progress.
 *
 * Hermetic, like boot-smoke.mjs: the page and every /static/** file are served
 * from THIS checkout, the backup + browse APIs are canned, everything else is
 * aborted. No running MC server, no real backup written.
 *
 * RUN: node tools/smoke/backup-panel-breakdown.mjs
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

const GB = 1024 ** 3;
const SIZE_PREVIEW = {
  generated_at: '2026-09-09T00:00:00Z',
  total_bytes: 48 * GB,
  warnings: [],
  categories: {
    records: { enabled: true, bytes: 160_000_000, files: 900, directories: [
      { path: 'C:\\mc\\data\\projects', bytes: 120_000_000, files: 700 },
      { path: 'C:\\Users\\x\\.claude\\agents', bytes: 40_000_000, files: 200 }] },
    artifacts: { enabled: true, bytes: 9_200_000, files: 40, directories: [
      { path: 'C:\\mc\\data\\media', bytes: 9_200_000, files: 40 }] },
    media: { enabled: true, bytes: 252_000_000, files: 120, directories: [
      { path: 'C:\\mc\\data\\uploads', bytes: 252_000_000, files: 120 }] },
    transcripts: { enabled: true, bytes: 3.6 * GB, files: 5000, directories: [
      { path: 'C:\\Users\\x\\.claude\\projects\\C--proj-a', bytes: 3.6 * GB, files: 5000 }] },
    unprotected: { enabled: true, bytes: 44.8 * GB, files: 90000, directories: [
      { path: 'C:\\Users\\x\\Projects\\DayTrading\\engulfing-scanner', bytes: 44.3 * GB, files: 88000, kind: 'nongit', project_id: 'scanner' },
      { path: 'C:\\Users\\x\\Projects\\ApexTrader', bytes: 63_700_000, files: 900, kind: 'checkout', project_id: 'apex' },
      { path: 'C:\\Users\\x\\Projects\\cad3d', bytes: 150_100_000, files: 700, kind: 'nongit', project_id: 'cad' }] },
    vault: { status: 'not_available' },
  },
};

// Job progress: two running ticks then done, so the bar has to actually move.
const JOB_TICKS = [
  { status: 'running', files_written: 12000, total_files: 96000, bytes_written: 6 * GB,
    total_bytes: 48 * GB, current_file: 'unprotected/nongit/scanner/data/ticks.csv', warnings_count: 1, result: null, error: null },
  { status: 'running', files_written: 48000, total_files: 96000, bytes_written: 24 * GB,
    total_bytes: 48 * GB, current_file: 'unprotected/nongit/scanner/data/more.csv', warnings_count: 2, result: null, error: null },
  { status: 'done', files_written: 96000, total_files: 96000, bytes_written: 48 * GB,
    total_bytes: 48 * GB, current_file: 'manifest.json', warnings_count: 2, error: null,
    result: { path: 'D:\\backups\\clayrune-2026-09-09-full.crbackup', files_written: 96000, warnings: [{ kind: 'oversize_skipped', path: 'x' }] } },
];

let createBody = null;
let tick = 0;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
// The first-run tour would otherwise sit on top of the panel under test.
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
    // Serve any static asset straight off disk — an enumerated map silently
    // drops a newly-added module, and a panel that never loaded would make
    // every assertion below pass by testing nothing.
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
  if (path === '/api/backup/size-preview') return json(SIZE_PREVIEW);
  if (path === '/api/backup/dest-dir') return json({ configured: null, effective: 'C:\\Users\\x\\.clayrune\\backups' });
  if (path === '/api/backup/list') return json({ backups: [] });
  if (path === '/api/browse/folders')
    return json({ path: 'C:\\Users\\x\\Backups', parent: 'C:\\Users\\x', home: 'C:\\Users\\x',
                  workspace_base: '', folders: [{ name: 'nightly', path: 'C:\\Users\\x\\Backups\\nightly' }] });
  if (path === '/api/backup/create') {
    createBody = JSON.parse(route.request().postData() || '{}');
    return json({ job_id: 'job-smoke-1', status: 'running' }, 202);
  }
  if (path.startsWith('/api/backup/create/status/')) {
    const body = JOB_TICKS[Math.min(tick, JOB_TICKS.length - 1)];
    tick++;
    return json({ job_id: 'job-smoke-1', started_at: '2026-09-09T00:00:00Z', ...body });
  }
  return route.abort();
});

const rowCount = () => page.evaluate(() =>
  document.querySelectorAll('#backup-body label').length);
// A breakdown row renders its path in a leaf <span>, its size in the sibling.
const visibleBreakdownPaths = () => page.evaluate(() =>
  [...document.querySelectorAll('#backup-body span')]
    .map(d => d.textContent.trim()).filter(t => /^[A-Za-z]:\\/.test(t)));

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openBackupSurface === 'function', { timeout: 20000 });
  await page.evaluate(() => window.openBackupSurface('backup'));
  await page.waitForSelector('#backup-body label', { timeout: 10000 });
  await page.waitForFunction(() => document.querySelectorAll('#backup-body label').length >= 5,
    { timeout: 10000 });

  const rows = await rowCount();
  rows >= 5 ? ok(`all ${rows} category rows render`) : fail(`only ${rows} category rows`);

  // ── 1. collapsed by default ──────────────────────────────────────────────
  const initial = await visibleBreakdownPaths();
  initial.length === 0
    ? ok('every category starts COLLAPSED — no breakdown rows on open')
    : fail(`${initial.length} breakdown rows were showing before any click: ${initial.slice(0, 2)}`);

  const carets = await page.$$('#backup-body label span[onclick*="_backupToggleExpand"]');
  carets.length === 5
    ? ok('all 5 categories carry a caret affordance')
    : fail(`expected 5 carets, found ${carets.length}`);

  // ── 2. every category expands with the SAME component ────────────────────
  for (const [cat, wantPath] of [
    ['unprotected', 'engulfing-scanner'], ['records', 'data\\projects'],
    ['artifacts', 'data\\media'], ['media', 'data\\uploads'],
    ['transcripts', '.claude\\projects'],
  ]) {
    await page.evaluate((c) => window._backupToggleExpand(c), cat);
    const shown = await visibleBreakdownPaths();
    shown.some(p => p.includes(wantPath))
      ? ok(`${cat}: breakdown expands and lists ${wantPath}`)
      : fail(`${cat}: expanded but ${wantPath} is not in ${JSON.stringify(shown)}`);
    await page.evaluate((c) => window._backupToggleExpand(c), cat);
    const after = await visibleBreakdownPaths();
    after.length === 0 ? ok(`${cat}: collapses again`) : fail(`${cat}: still showing ${after.length} rows`);
  }

  // The caret must not tick the checkbox it sits inside (it lives in a <label>).
  const before = await page.evaluate(() =>
    [...document.querySelectorAll('#backup-body input[type=checkbox]')].map(c => c.checked));
  await page.locator('#backup-body label span[onclick*="_backupToggleExpand"]').last().click();
  const afterClick = await page.evaluate(() =>
    [...document.querySelectorAll('#backup-body input[type=checkbox]')].map(c => c.checked));
  const expandedNow = (await visibleBreakdownPaths()).length;
  JSON.stringify(before) === JSON.stringify(afterClick) && expandedNow > 0
    ? ok('clicking the caret expands WITHOUT unticking the category')
    : fail(`caret click changed checkboxes ${JSON.stringify(before)}→${JSON.stringify(afterClick)} (rows: ${expandedNow})`);
  await page.locator('#backup-body label span[onclick*="_backupToggleExpand"]').last().click();

  // ── 3. destination picker reuses the existing folder picker ──────────────
  const browse = page.locator('#backup-body button.btn-browse').first();
  await browse.count() > 0 ? ok('Destination has a Browse… button') : fail('no Browse button on Destination');
  await browse.click();
  await page.waitForSelector('#fp-overlay .fp-dialog', { timeout: 5000 });
  ok('Browse opens the existing server-side folder picker (#fp-overlay)');
  const title = (await page.textContent('#fp-overlay .fp-title') || '').trim();
  /backup destination/i.test(title) ? ok(`picker is titled for this job ("${title}")`)
    : fail(`picker title is still the project one: "${title}"`);
  await page.click('#fp-select');   // "Use this folder"
  await page.waitForTimeout(300);
  const destVal = await page.inputValue('#backup-body input.path-input');
  destVal === 'C:\\Users\\x\\Backups'
    ? ok('chosen folder lands in the Destination field')
    : fail(`Destination field holds ${JSON.stringify(destVal)}`);

  // ── 4. real progress, not a dead button ─────────────────────────────────
  await page.evaluate(() => window._backupCreate());
  await page.waitForSelector('#backup-job-progress div', { timeout: 5000 });
  createBody && createBody.async === true
    ? ok('create posts async:true (job id back immediately)')
    : fail(`create body did not request async: ${JSON.stringify(createBody)}`);
  createBody && createBody.dest_dir === 'C:\\Users\\x\\Backups'
    ? ok('create carries the picked destination')
    : fail(`create dest_dir = ${JSON.stringify(createBody && createBody.dest_dir)}`);

  const firstText = (await page.textContent('#backup-job-progress')).replace(/\s+/g, ' ').trim();
  /12\.5%|%/.test(firstText) && /GB of 48/.test(firstText)
    ? ok(`progress shows bytes + a percentage — "${firstText.slice(0, 70)}"`)
    : fail(`progress text is not byte/percent shaped: "${firstText}"`);
  const barWidth = await page.evaluate(() =>
    document.querySelector('#backup-job-progress div div div')?.style.width || '');
  /^\d+%$/.test(barWidth) && barWidth !== '0%'
    ? ok(`progress bar is filled to ${barWidth}`)
    : fail(`progress bar width is ${JSON.stringify(barWidth)}`);

  await page.waitForFunction(
    () => /Created/.test(document.querySelector('#backup-body')?.textContent || ''), { timeout: 15000 });
  const done = (await page.textContent('#backup-body')).replace(/\s+/g, ' ');
  /clayrune-2026-09-09-full\.crbackup/.test(done)
    ? ok('job completion swaps the bar for the real result (archive path + warnings)')
    : fail('completed job never rendered its result');

  if (process.env.MC_SMOKE_SHOT) {
    await page.evaluate(() => window._backupToggleExpand('unprotected'));
    await page.screenshot({ path: process.env.MC_SMOKE_SHOT });
    ok('screenshot written to ' + process.env.MC_SMOKE_SHOT);
  }
  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
