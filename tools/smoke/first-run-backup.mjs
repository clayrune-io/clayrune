#!/usr/bin/env node
/**
 * "Protect your work" first-run step (MC-982, backlog ee381c55) — backup
 * destination, an optional immediate backup, and the automatic-cadence
 * preference, placed after 'essentials-detail' and before 'tour'.
 *
 * Hermetic, same shape as first-run-setup-gate.mjs: route-mocked /api/*,
 * real index.html + real static/js/*.js, no network, no running MC server.
 *
 * RUN
 *   cd tools/smoke && node first-run-backup.mjs
 * Exit 0 = every assertion holds; 1 = a regression or a page error fired.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};

// One vendor installed+signed-in so the 'connections' step skips itself and
// every scenario reaches 'protect' in the same 4 clicks.
const ONE_PROVIDER_OK = {
  providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }],
  default: 'claude',
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser;
let scenarioIdx = 0;

async function scenario(name, { config, configPutHandler = null, dest = { configured: null, effective: '/home/user/.clayrune/backups' } }, run) {
  scenarioIdx++;
  console.log(`\n[${scenarioIdx}] ${name}`);
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const configPuts = [];
  const backupCreateCalls = [];
  let statusPolls = 0;

  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLAYRUNE_PROJECT]) });
    if (path === '/api/config') {
      if (req.method() === 'PUT') {
        let body = {};
        try { body = JSON.parse(req.postData() || '{}'); } catch (_) {}
        configPuts.push(body);
        if (configPutHandler) {
          const custom = configPutHandler(body);
          if (custom) return route.fulfill(custom);
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(config) });
    }
    if (path === '/api/backup/dest-dir') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dest) });
    if (path === '/api/backup/create' && req.method() === 'POST') {
      let body = {};
      try { body = JSON.parse(req.postData() || '{}'); } catch (_) {}
      backupCreateCalls.push(body);
      return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-smoke-1', status: 'running' }) });
    }
    if (path === '/api/backup/create/status/job-smoke-1') {
      statusPolls++;
      // Second poll onward reports done — exercises the poll loop at least once.
      if (statusPolls < 2) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-smoke-1', status: 'running', files_written: 3, total_files: 10, bytes_written: 300, total_bytes: 1000 }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        job_id: 'job-smoke-1', status: 'done',
        result: { path: '/home/user/.clayrune/backups/clayrune-backup.zip', files_written: 42, warnings: [], bytes: 12345 },
      }) });
    }
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ONE_PROVIDER_OK) });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ configured: false }) });
    if (path === '/api/system/update/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] }) });
    return route.abort();
  });

  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await run(page, { configPuts, backupCreateCalls });
  } finally {
    if (pageErrors.length) fail(`[${name}] uncaught exception(s): ${pageErrors.join(' | ')}`);
    await ctx.close();
  }
}

// Walks Welcome -> (connections skips) -> essentials A -> B -> C -> protect.
async function walkToProtectStep(page) {
  await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
  await page.click('#setup-overlay .wt-btn-primary'); // Get started -> essentials A (connections skipped)
  await page.waitForTimeout(150);
  await page.click('#setup-overlay .wt-btn-primary'); // Next -> essentials-phone
  await page.waitForTimeout(150);
  await page.click('#setup-overlay button:has-text("Not now")'); // -> essentials-detail
  await page.waitForTimeout(150);
  await page.click('#setup-overlay .wt-btn-primary'); // Next -> protect
  await page.waitForTimeout(250); // onEnter's dest-dir fetch + re-render
  const title = await page.locator('#setup-overlay .wt-title').textContent().catch(() => '');
  if (title.trim() !== 'Protect your work') { fail(`expected 'Protect your work' step, got: "${title.trim()}"`); return false; }
  ok('reached the "Protect your work" step after essentials-detail');
  return true;
}

try {
  browser = await chromium.launch();

  // ── 1. Step renders: destination shown, backup button, cadence, restore-points copy
  await scenario('step renders with destination, back-up-now, cadence and restore-points copy', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    dest: { configured: null, effective: '/home/user/.clayrune/backups' },
  }, async (page) => {
    if (!(await walkToProtectStep(page))) return;
    const destPlaceholder = await page.locator('#setup-overlay input.path-input').getAttribute('placeholder');
    if (destPlaceholder !== '/home/user/.clayrune/backups') fail(`destination placeholder should show the effective (unset) path, got: "${destPlaceholder}"`);
    else ok('unset destination shows the effective ~/.clayrune/backups path');
    if (!(await page.locator('#setup-overlay button:has-text("Back up now")').count())) fail('"Back up now" button missing');
    else ok('"Back up now" button present');
    const cadenceBtns = page.locator('#setup-backup-schedule-seg button');
    if ((await cadenceBtns.count()) !== 3) fail(`expected 3 cadence options, got ${await cadenceBtns.count()}`);
    else ok('cadence control has 3 options (Off/Daily/Weekly)');
    const weeklyActive = await page.locator('#setup-backup-schedule-seg button[data-cadence="weekly"]').getAttribute('class');
    if (!/active/.test(weeklyActive || '')) fail(`Weekly should be pre-selected, class was: "${weeklyActive}"`);
    else ok('Weekly is pre-selected');
    const cardText = await page.locator('#setup-overlay .wt-card').textContent();
    if (!/restore point/i.test(cardText)) fail('no restore-points explanation on the step');
    else ok('restore-points paragraph present');
    if (!/10/.test(cardText)) fail('restore-points copy does not mention the 10-per-project retention');
    else ok('restore-points copy mentions "10"');
    if (/—/.test(cardText)) fail('em-dash found in step copy');
    else ok('no em-dashes in step copy');
  });

  // ── 2. Bad destination surfaces the server's validation error inline ────
  await scenario('bad destination shows the validation error inline', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    configPutHandler: (body) => {
      if ('backup_dest_dir' in body) {
        return { status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'backup_dest_dir cannot be inside the Clayrune install or a project data folder' }) };
      }
      return null;
    },
  }, async (page) => {
    if (!(await walkToProtectStep(page))) return;
    const input = page.locator('#setup-overlay input.path-input');
    await input.fill('C:\\Users\\smoke\\mission-control\\data\\projects');
    await input.blur();
    await page.waitForTimeout(200);
    const errText = await page.locator('#setup-overlay').textContent();
    if (!/cannot be inside the Clayrune install/.test(errText)) fail(`server validation error text not surfaced: "${errText.slice(0, 200)}"`);
    else ok('server-side validate_backup_dest_dir error text surfaced inline');
  });

  // ── 3. Good destination persists through the same /api/config path ──────
  await scenario('good destination saves through PUT /api/config', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
  }, async (page, { configPuts }) => {
    if (!(await walkToProtectStep(page))) return;
    const input = page.locator('#setup-overlay input.path-input');
    await input.fill('D:\\Backups\\clayrune');
    await input.blur();
    await page.waitForTimeout(200);
    const saved = configPuts.filter((p) => p.backup_dest_dir === 'D:\\Backups\\clayrune');
    if (saved.length !== 1) fail(`expected exactly one PUT /api/config {backup_dest_dir:'D:\\\\Backups\\\\clayrune'}, got ${saved.length}: ${JSON.stringify(configPuts)}`);
    else ok('PUT /api/config sent with the new backup_dest_dir');
    const errText = await page.locator('#setup-overlay').textContent();
    if (/cannot be inside/.test(errText)) fail('a stale validation error is still shown after a good save');
    else ok('no validation error shown after a good save');
  });

  // ── 4. "Back up now" starts the async job, shows progress, then result ──
  await scenario('"Back up now" calls the async backup route and shows progress then result', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
  }, async (page, { backupCreateCalls }) => {
    if (!(await walkToProtectStep(page))) return;
    await page.click('#setup-overlay button:has-text("Back up now")');
    await page.waitForTimeout(150);
    if (backupCreateCalls.length !== 1) fail(`expected exactly one POST /api/backup/create, got ${backupCreateCalls.length}`);
    else ok('POST /api/backup/create called once');
    if ('categories' in backupCreateCalls[0]) fail(`Back up now must send no 'categories' key (full default) — got: ${JSON.stringify(backupCreateCalls[0])}`);
    else ok('no categories key sent — full default backup (standing position: everything included)');
    if (backupCreateCalls[0].async !== true) fail('Back up now did not request an async job');
    else ok('requested async:true so setup never blocks on the write');
    // First poll (running): progress bar visible.
    await page.waitForTimeout(150);
    const progressVisible = await page.locator('#setup-backup-job-progress').textContent();
    if (!/\d+\/\d+ files/.test(progressVisible)) fail(`progress not shown while running: "${progressVisible}"`);
    else ok(`progress shown while running: "${progressVisible.trim()}"`);
    // Second poll (700ms later): done, result rendered.
    await page.waitForFunction(() => /Backed up \d+ file/.test(document.getElementById('setup-overlay').textContent), { timeout: 5000 });
    ok('result shown once the job reports done (42 files)');
  });

  // ── 5. Cadence pick sends backup_schedule; not yet in _CONFIG_EDITABLE_KEYS on this branch ──
  await scenario('cadence pick sends backup_schedule through the same config path', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
  }, async (page, { configPuts }) => {
    if (!(await walkToProtectStep(page))) return;
    await page.click('#setup-backup-schedule-seg button[data-cadence="daily"]');
    await page.waitForTimeout(150);
    const sent = configPuts.filter((p) => p.backup_schedule === 'daily');
    if (sent.length !== 1) fail(`expected one PUT /api/config {backup_schedule:'daily'}, got ${sent.length}: ${JSON.stringify(configPuts)}`);
    else ok('PUT /api/config sent with backup_schedule=\'daily\' (server-side persistence is MC-983, built in parallel — this only proves the client sends the key on the shared config path)');
    const activeClass = await page.locator('#setup-backup-schedule-seg button[data-cadence="daily"]').getAttribute('class');
    if (!/active/.test(activeClass || '')) fail('Daily did not become the active selection client-side');
    else ok('Daily becomes the active selection immediately, regardless of server persistence');
  });

  // ── 6. Step is skippable like the other essentials steps ────────────────
  await scenario('protect step is skippable', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
  }, async (page) => {
    if (!(await walkToProtectStep(page))) return;
    if (await page.locator('#setup-overlay .wt-btn-skip, #setup-overlay .setup-btn-secondary:has-text("Skip")').count() === 0
        && !(await page.locator('#setup-overlay .wt-btn-primary').isEnabled())) {
      fail('protect step cannot be advanced past without filling anything in');
      return;
    }
    await page.click('#setup-overlay .wt-btn-primary'); // Next, with nothing filled in
    await page.waitForTimeout(200);
    const title = await page.locator('#setup-overlay .wt-title').textContent();
    if (title.trim() !== 'Take the tour?') fail(`Next on an untouched protect step should reach the tour offer, got: "${title.trim()}"`);
    else ok('protect step advances to the tour offer with nothing filled in (skippable, like the other essentials steps)');
  });

  // ── 7. Pre-selected Weekly persists on step-enter, no click needed ───────
  // Regression coverage for the review of c392e85: a user who accepts the
  // highlighted default and clicks Next (the common path) must still get an
  // explicit backup_schedule saved, not silently leave the key unset.
  await scenario('pre-selected Weekly is saved automatically when the step is first shown', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
  }, async (page, { configPuts }) => {
    if (!(await walkToProtectStep(page))) return;
    const sent = configPuts.filter((p) => p.backup_schedule === 'weekly');
    if (sent.length !== 1) fail(`expected one auto PUT /api/config {backup_schedule:'weekly'} on step-enter, got ${sent.length}: ${JSON.stringify(configPuts)}`);
    else ok('Weekly auto-persisted on step-enter with no click');
  });

  // ── 8. A saved schedule is shown and left alone, never reset to Weekly ──
  await scenario('a saved backup_schedule is shown as-is and not overwritten', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced', backup_schedule: 'daily' },
  }, async (page, { configPuts }) => {
    if (!(await walkToProtectStep(page))) return;
    const dailyActive = await page.locator('#setup-overlay #setup-backup-schedule-seg button[data-cadence="daily"]').getAttribute('class');
    if (!/active/.test(dailyActive || '')) fail(`saved 'daily' should be pre-selected, class was: "${dailyActive}"`);
    else ok('saved backup_schedule=\'daily\' is shown as the active selection, not reset to Weekly');
    if (configPuts.some((p) => 'backup_schedule' in p)) fail(`an already-saved schedule must not be re-PUT on step-enter, got: ${JSON.stringify(configPuts)}`);
    else ok('no auto-PUT fired — the saved choice was left alone');
  });

  // ── 9. A cadence PUT that genuinely fails surfaces an inline note ───────
  await scenario('a failed cadence save shows an inline error, not silence', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    configPutHandler: (body) => {
      if ('backup_schedule' in body) return { status: 500, contentType: 'application/json', body: '{}' };
      return null;
    },
  }, async (page) => {
    if (!(await walkToProtectStep(page))) return; // step-enter's own auto-persist already fails here
    await page.waitForTimeout(150);
    const cardText = await page.locator('#setup-overlay').textContent();
    if (!/Could not save/.test(cardText)) fail(`expected an inline "Could not save" note after the PUT failed, card text: "${cardText.slice(0, 300)}"`);
    else ok('a failed backup_schedule save surfaces an inline note instead of being swallowed');
  });

  console.log(bad === 0 ? '\n✅ PASS — "Protect your work" first-run step: render, validation error, save, back-up-now, cadence, skippable.'
                        : `\n❌ FAIL — ${bad} assertion(s) failed.`);
} finally {
  if (browser) await browser.close();
}
process.exit(bad === 0 ? 0 : 1);
