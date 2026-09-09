#!/usr/bin/env node
/**
 * Backup panel — the defects Ron reported against the shipped panel.
 *
 * WHY THIS EXISTS
 * ---------------
 * Round one (four defects):
 * 1. "Unprotected work files" auto-expanded its per-directory list with no way
 *    to close it — dozens of rows on a real install, pushing Create off-screen.
 * 2. Only 'unprotected' had a breakdown at all; the other four categories
 *    showed a total with no way to see what was in it.
 * 3. The destination was a bare text field — no picker, though Clayrune already
 *    ships a server-side folder picker that works over remote access.
 * 4. Create was synchronous: a ~48GB archive parked on a dead "Creating…"
 *    button for minutes with no progress.
 *
 * Round two (this pass):
 * 5. No way to STOP a running backup — a 48GB write the user cannot abort is
 *    worse than a blocking one. Cancel is cooperative and ends in its own
 *    'cancelled' state, never 'failed'.
 * 6. The destination moved OUT of the form and INTO the create flow: tick
 *    categories, hit Create, and the picker opens then — seeded at the
 *    Settings → System default. One numbered flow, exactly one accent button.
 * 7. Reopening the panel mid-run lost the job entirely, because the job_id
 *    lived only in the closing tab's JS. The panel now re-discovers it from
 *    the server (GET /api/backup/jobs), and shows the last backup's details
 *    when nothing is running.
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

// One archive already on disk, so the panel has a "last backup" to describe.
const LAST_BACKUP = {
  path: 'D:\\backups\\clayrune-2026-09-08-full.crbackup',
  bytes: 41.2 * GB, created_at: '2026-09-08T22:10:00Z', kind: 'full', format: 1,
  categories: { records: true, artifacts: true, media: true, transcripts: true,
                unprotected: true, vault: false },
  file_count: 95_000, warning_count: 2,
};

// Job progress: two running ticks then done, so the bar has to actually move.
const RUNNING_TICK = {
  status: 'running', files_written: 12000, total_files: 96000, bytes_written: 6 * GB,
  total_bytes: 48 * GB, current_file: 'unprotected/nongit/scanner/data/ticks.csv',
  warnings_count: 1, result: null, error: null, cancel_requested: false,
};
const JOB_TICKS = [
  RUNNING_TICK,
  { ...RUNNING_TICK, files_written: 48000, bytes_written: 24 * GB,
    current_file: 'unprotected/nongit/scanner/data/more.csv', warnings_count: 2 },
  { status: 'done', files_written: 96000, total_files: 96000, bytes_written: 48 * GB,
    total_bytes: 48 * GB, current_file: 'manifest.json', warnings_count: 2, error: null,
    result: { path: 'D:\\backups\\clayrune-2026-09-09-full.crbackup', files_written: 96000,
              bytes: 41.9 * GB, warnings: [{ kind: 'oversize_skipped', path: 'x' }] } },
];

let createBody = null;
let createCalls = 0;
let cancelCalls = [];
let tick = 0;
let mode = 'progress';      // 'progress' walks JOB_TICKS; 'hold' stays running
let cancelled = false;
let activeJobs = [];        // what GET /api/backup/jobs reports as live
let recentJobs = [];

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
  if (path === '/api/backup/list') return json({ backups: [LAST_BACKUP] });
  if (path === '/api/backup/jobs') return json({ active: activeJobs, recent: recentJobs });
  if (path === '/api/browse/folders')
    return json({ path: 'C:\\Users\\x\\Backups', parent: 'C:\\Users\\x', home: 'C:\\Users\\x',
                  workspace_base: '', folders: [{ name: 'nightly', path: 'C:\\Users\\x\\Backups\\nightly' }] });
  if (path === '/api/backup/create') {
    createCalls++;
    createBody = JSON.parse(route.request().postData() || '{}');
    return json({ job_id: 'job-smoke-1', status: 'running' }, 202);
  }
  if (path.startsWith('/api/backup/create/cancel/')) {
    cancelCalls.push(decodeURIComponent(path.split('/').pop()));
    cancelled = true;
    return json({ job_id: 'job-smoke-1', status: 'cancelling', cancelled: true });
  }
  if (path.startsWith('/api/backup/create/status/')) {
    const jobId = decodeURIComponent(path.split('/').pop());
    if (cancelled)
      return json({ job_id: jobId, started_at: '2026-09-09T00:00:00Z', ...RUNNING_TICK,
                    status: 'cancelled', result: null, error: null,
                    cancelled_reason: 'backup cancelled after 12000 of 96000 files' });
    if (mode === 'hold')
      return json({ job_id: jobId, started_at: '2026-09-09T00:00:00Z', ...RUNNING_TICK });
    const body = JOB_TICKS[Math.min(tick, JOB_TICKS.length - 1)];
    tick++;
    return json({ job_id: jobId, started_at: '2026-09-09T00:00:00Z', ...body });
  }
  return route.abort();
});

const rowCount = () => page.evaluate(() =>
  document.querySelectorAll('#backup-body label').length);
// A breakdown row renders its path in a leaf <span>, its size in the sibling.
const visibleBreakdownPaths = () => page.evaluate(() =>
  [...document.querySelectorAll('#backup-body span')]
    .map(d => d.textContent.trim()).filter(t => /^[A-Za-z]:\\/.test(t)));
const bodyText = async () => (await page.textContent('#backup-body')).replace(/\s+/g, ' ');
const reopenPanel = async () => {
  await page.evaluate(() => window.closeModalById('__backup'));
  await page.waitForTimeout(100);
  await page.evaluate(() => window.openBackupSurface('backup'));
  await page.waitForSelector('#backup-body label', { timeout: 10000 });
};

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
    // The "last backup" card also prints a path — it is not a breakdown row.
    const leftover = after.filter(p => !p.includes('clayrune-2026-09-08'));
    leftover.length === 0 ? ok(`${cat}: collapses again`) : fail(`${cat}: still showing ${leftover.length} rows`);
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

  // ── 6a. no Destination field on the form, one accent button ─────────────
  const destFields = await page.locator('#backup-body input.path-input').count();
  destFields === 0
    ? ok('the Destination field is GONE from the Backup form')
    : fail(`${destFields} destination input(s) still sit on the create form`);
  const accents = await page.locator('#backup-body button.btn-add').count();
  accents === 1
    ? ok('exactly one accent button (the final action) on the tab')
    : fail(`${accents} accent buttons on the Backup tab`);
  const idle = await bodyText();
  /1\. What to include/.test(idle) && /2\. Label/.test(idle)
    ? ok('inputs are numbered top-down (1. What to include, 2. Label)')
    : fail(`inputs are not numbered: "${idle.slice(0, 120)}"`);

  // ── 7b. last backup details, from the archive's own manifest ────────────
  /Last backup/.test(idle) && /clayrune-2026-09-08-full\.crbackup/.test(idle)
    ? ok('last backup is surfaced at the top with its path')
    : fail('no last-backup summary on the Backup tab');
  /41\.2 GB/.test(idle) && /records, artifacts/.test(idle) && /95000 files/.test(idle)
    ? ok('last backup reports size, categories and file count')
    : fail(`last-backup summary is missing size/categories: "${idle.slice(0, 200)}"`);

  // ── 3 + 6b. the picker opens FROM Create, seeded at the default ─────────
  await page.click('#backup-body button.btn-add');
  await page.waitForSelector('#fp-overlay .fp-dialog', { timeout: 5000 });
  ok('Create opens the existing server-side folder picker (#fp-overlay)');
  const title = (await page.textContent('#fp-overlay .fp-title') || '').trim();
  /backup destination/i.test(title) ? ok(`picker is titled for this job ("${title}")`)
    : fail(`picker title is still the project one: "${title}"`);
  createCalls === 0
    ? ok('nothing is written until a folder is confirmed')
    : fail('create fired before the user picked a folder');

  // ── 4. real progress, not a dead button ─────────────────────────────────
  await page.click('#fp-select');   // "Use this folder"
  await page.waitForSelector('#backup-job-progress div', { timeout: 5000 });
  createBody && createBody.async === true
    ? ok('create posts async:true (job id back immediately)')
    : fail(`create body did not request async: ${JSON.stringify(createBody)}`);
  createBody && createBody.dest_dir === 'C:\\Users\\x\\Backups'
    ? ok('create carries the folder chosen in the flow')
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
  const done = await bodyText();
  /clayrune-2026-09-09-full\.crbackup/.test(done)
    ? ok('job completion swaps the bar for the real result (archive path + warnings)')
    : fail('completed job never rendered its result');

  // ── 5. cancel a running job ─────────────────────────────────────────────
  mode = 'hold'; cancelled = false; tick = 0; cancelCalls = [];
  await reopenPanel();
  await page.click('#backup-body button.btn-add');
  await page.waitForSelector('#fp-overlay .fp-dialog', { timeout: 5000 });
  await page.click('#fp-select');
  await page.waitForSelector('#backup-cancel-btn', { timeout: 5000 });
  ok('a Cancel button sits next to the progress bar while a job runs');
  (await page.locator('#backup-body button.btn-add').count()) === 0
    ? ok('the Create button is replaced while the write is in flight')
    : fail('Create is still clickable during a running backup');

  await page.click('#backup-cancel-btn');
  await page.waitForFunction(
    () => /cancelled/i.test(document.querySelector('#backup-body')?.textContent || ''), { timeout: 15000 });
  cancelCalls.length === 1
    ? ok(`cancel POSTs to the cancel endpoint (job ${cancelCalls[0]})`)
    : fail(`cancel endpoint called ${cancelCalls.length} times`);
  const afterCancel = await bodyText();
  /cancelled/i.test(afterCancel) && !/failed/i.test(afterCancel)
    ? ok('a cancelled job reads as CANCELLED, not failed')
    : fail(`cancel was reported as a failure: "${afterCancel.slice(0, 160)}"`);
  /partial archive was deleted/i.test(afterCancel)
    ? ok('the panel says the partial archive was cleaned up')
    : fail('no reassurance that the .partial was removed');
  (await page.locator('#backup-body button.btn-add').count()) === 1
    ? ok('Create comes back after a cancel')
    : fail('Create did not return after cancelling');

  // ── 7a. reopening reattaches to a job this tab never started ────────────
  mode = 'hold'; cancelled = false;
  activeJobs = [{ job_id: 'job-from-another-tab', status: 'running', started_at: '2026-09-09T01:00:00Z',
                  ...RUNNING_TICK }];
  await reopenPanel();
  await page.waitForSelector('#backup-cancel-btn', { timeout: 8000 });
  ok('a reopened panel reattaches to the running job (progress + Cancel, no new create)');
  const reattached = await bodyText();
  /GB of 48/.test(reattached)
    ? ok('reattached panel renders the live byte progress from the server')
    : fail(`reattached panel shows no progress: "${reattached.slice(0, 160)}"`);
  createCalls === 2
    ? ok('reattaching started NO new backup')
    : fail(`reattach fired ${createCalls - 2} extra create call(s)`);

  // ── 7c. reopening after it finished shows the result, not a blank ───────
  activeJobs = [];
  recentJobs = [{ job_id: 'job-from-another-tab', status: 'done', started_at: '2026-09-09T01:00:00Z',
                  finished_at: '2026-09-09T01:40:00Z', ...JOB_TICKS[2] }];
  await reopenPanel();
  await page.waitForFunction(
    () => /finished while the panel was closed/i.test(
      document.querySelector('#backup-body')?.textContent || ''), { timeout: 8000 });
  ok('a panel reopened after the job finished shows its result, not a blank form');

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
