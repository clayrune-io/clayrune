#!/usr/bin/env node
/**
 * Scheduler -> optional run condition "Only spend leftover weekly allowance".
 *
 * Pins the UI half of mc/schedule_conditions.py: the form checkbox reveals the
 * lead-time / ceiling / engine fields, a saved condition round-trips into the
 * Edit form, unchecking sends `run_condition: null` (the only way to clear it),
 * Once hides the section (the server refuses it there), the card shows a badge,
 * and the Runs panel lists skipped ticks with their reason above real runs.
 *
 * Hermetic, same idiom as settings-backup-schedule.mjs: page + static assets
 * served from THIS checkout, API canned in-memory, everything else aborted.
 *
 * RUN: node tools/smoke/scheduler-run-condition.mjs
 *      SMOKE_SHOT_DIR=<dir> also writes screenshots of the form and the card.
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const projects = JSON.parse(readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8'))
  .map((p) => ({ ...p, project_path: 'C:/smoke/alpha' }));

const COND = { type: 'spend_leftover_weekly', lead_hours: 12, ceiling_percent: 80, engine: '' };
let schedules = [{
  id: 'sc1', enabled: true, project_id: projects[0].id, project_name: projects[0].name,
  task: 'Burn down the docs backlog', workflow_id: '', character: '', description: '',
  continue_session: true, schedule_type: 'interval', interval_minutes: 60, time: '09:00',
  days: [], run_at: '', cron_expr: '', last_run: null,
  next_run: new Date(Date.now() + 3600e3).toISOString(), run_condition: COND,
  character_display: null, workflow_display: null,
}];
const skips = [
  { ts: new Date(Date.now() - 600e3).toISOString(), kind: 'condition',
    reason: 'claude weekly window resets in 30.0h, outside the 12h lead time' },
  { ts: new Date(Date.now() - 4200e3).toISOString(), kind: 'condition',
    reason: 'claude weekly usage is missing, invalid, stale or already reset; not spending blind' },
];
let writes = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const url = new URL(req.url());
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

  if (path === '/api/projects') return json(projects);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config') return json({});
  if (path === '/api/characters') return json([]);
  if (path === '/api/schedules' && req.method() === 'GET') return json(schedules);
  if (path === '/api/schedules' && req.method() === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    writes.push({ method: 'POST', body });
    return json({ ...body, id: 'new1' }, 201);
  }
  if (path.startsWith('/api/schedules/') && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    writes.push({ method: 'PUT', body });
    return json({ ...schedules[0], ...body });
  }
  if (path === '/api/schedule/sc1/runs')
    return json({ runs: [], total: 0, offset: 0, limit: 50, skips });
  return route.abort();
});

const shot = async (name) => { if (SHOT_DIR) await page.screenshot({ path: join(SHOT_DIR, name) }); };

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openScheduler === 'function', { timeout: 20000 });
  await page.evaluate(() => window.openScheduler());
  await page.waitForSelector('.schedule-card', { timeout: 10000 });

  // ── 1. Card badge + skipped ticks in Runs ──────────────────────────────
  const badge = await page.evaluate(() =>
    document.querySelector('.schedule-card-meta')?.textContent || '');
  check(/Leftover allowance only/.test(badge) && badge.includes('12h') && badge.includes('80%'),
    'card shows the leftover-allowance badge with its lead time and ceiling');
  await page.evaluate(() => window.toggleScheduleRuns('sc1', 'smoke_alpha'));
  await page.waitForSelector('.sched-skip-row', { timeout: 5000 });
  const skipTexts = await page.evaluate(() =>
    [...document.querySelectorAll('.sched-skip-row')].map((r) => r.textContent.replace(/\s+/g, ' ')));
  check(skipTexts.length === 2 && skipTexts[0].includes('outside the 12h lead time')
    && skipTexts[1].includes('not spending blind'),
    'Runs lists both skipped ticks with their reasons, newest first');
  await shot('scheduler-card-and-skips.png');

  // ── 2. Edit: condition prefilled, section visible ──────────────────────
  await page.evaluate(() => window.editSchedule('sc1'));
  await page.waitForSelector('#sched-cond-on', { timeout: 5000 });
  const state = await page.evaluate(() => ({
    on: document.getElementById('sched-cond-on').checked,
    fieldsShown: document.getElementById('sched-cond-fields').style.display !== 'none',
    lead: document.getElementById('sched-cond-lead').value,
    ceiling: document.getElementById('sched-cond-ceiling').value,
  }));
  check(state.on && state.fieldsShown && state.lead === '12' && state.ceiling === '80',
    'Edit form opens with the saved condition checked and prefilled (12h / 80%)');
  await page.locator('#sched-condition-area').scrollIntoViewIfNeeded();
  await shot('scheduler-form-condition-set.png');

  // ── 3. Unchecking clears it: PUT carries run_condition: null ───────────
  writes = [];
  await page.evaluate(() => { document.getElementById('sched-cond-on').click(); });
  const hidden = await page.evaluate(() =>
    document.getElementById('sched-cond-fields').style.display === 'none');
  check(hidden, 'unchecking hides the condition fields');
  await page.evaluate(() => window.saveSchedule());
  await page.waitForFunction(() => !document.querySelector('#sched-cond-on'), { timeout: 5000 });
  check(writes.length === 1 && writes[0].method === 'PUT' && writes[0].body.run_condition === null,
    'saving with it unchecked sends run_condition: null');

  // ── 4. New schedule: checking it sends the edited values ───────────────
  writes = [];
  await page.evaluate(() => window.showScheduleForm());
  await page.waitForSelector('#sched-cond-on', { timeout: 5000 });
  check(await page.evaluate(() => !document.getElementById('sched-cond-on').checked
      && document.getElementById('sched-cond-fields').style.display === 'none'),
    'a new schedule starts with the condition off');
  await page.evaluate(() => {
    document.getElementById('sched-task').value = 'spend it';
    document.getElementById('sched-cond-on').click();
    document.getElementById('sched-cond-lead').value = '6';
    document.getElementById('sched-cond-ceiling').value = '70';
    document.getElementById('sched-cond-engine').value = 'codex';
  });
  await page.evaluate(() => window.saveSchedule());
  await page.waitForFunction(() => !document.querySelector('#sched-cond-on'), { timeout: 5000 });
  const sent = writes[0]?.body?.run_condition;
  check(writes[0]?.method === 'POST' && sent && sent.type === 'spend_leftover_weekly'
    && sent.lead_hours === 6 && sent.ceiling_percent === 70 && sent.engine === 'codex',
    'creating with it checked sends type / 6h / 70% / codex');

  // ── 5. Once hides the section and never sends a condition ──────────────
  await page.evaluate(() => window.showScheduleForm());
  await page.waitForSelector('#sched-cond-on', { timeout: 5000 });
  await page.evaluate(() => window.setSchedType('once'));
  check(await page.evaluate(() =>
    document.getElementById('sched-condition-area').style.display === 'none'),
    'choosing Once hides the condition section');
  await page.evaluate(() => window.setSchedType('daily'));
  check(await page.evaluate(() =>
    document.getElementById('sched-condition-area').style.display !== 'none'),
    'choosing a repeating type shows it again');

  check(pageErrors.length === 0, 'no uncaught page errors' + (pageErrors.length ? ': ' + pageErrors.join(' | ') : ''));
} catch (e) {
  fail('harness error: ' + (e && e.stack || e));
}

await browser.close();
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nscheduler-run-condition: all checks passed');
