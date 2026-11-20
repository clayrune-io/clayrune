#!/usr/bin/env node
/**
 * Project "Safety" dialog (backlog ecc5a845, MC-1179 pass B14 / BX4).
 *
 * WHY THIS EXISTS
 * ----------------
 * mc/project_safety_settings.py lists the keys an agent may not write. Seven of
 * them (merge_requires_review, cold_review_required, verify_command,
 * verify_timeout_seconds, worktree_isolation, worktree_base_branch,
 * worktree_shared_runtime) had no control anywhere under static/, so the merge
 * gate, the cold reviewer and the review requirement could only be switched on
 * by hand-editing data/projects/<id>.json. static/js/project-safety-settings.js
 * adds one dialog, opened from the project window's menu, that reads the stored
 * value, shows the effective default, and saves through the existing
 * POST /api/project/<id>.
 *
 * This asserts, against the real index.html + static/js + static/css and a stub
 * API that keeps the project record in memory:
 *   - the menu has a Safety entry and it opens the dialog;
 *   - a project with nothing set shows the true defaults (gates off, timeout
 *     1200, isolation on, base branch falls back to the sync branch);
 *   - a Save with nothing changed sends NO request;
 *   - changing every control and saving POSTs exactly those keys, with the
 *     right types, and nothing else;
 *   - the reloaded dialog shows the saved values;
 *   - clearing sends null / [] / '' so a setting can be taken back;
 *   - a bad timeout and a bad branch are refused in the dialog before any POST;
 *   - a refused write (403) keeps the dialog open and shows the server's error;
 *   - with the global worktree switch off, the dialog says so.
 *
 * Hermetic: no server. RUN  node tools/smoke/project-safety-settings.mjs
 * Exit 0 = all pass; 1 = a regression.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_project_safety';
const STATIC = loadStaticJsCss(REPO_ROOT);

const BASE_PROJECT = {
  id: PID, name: 'Safety Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-11-19T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
  code_sync_branch: 'trunk',
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const eq = (label, got, want) =>
  JSON.stringify(got) === JSON.stringify(want)
    ? ok(`${label}: ${JSON.stringify(got)}`)
    : fail(`${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

/** One fake server per page: the project record lives here, POSTs mutate it. */
function makeServer({ globalWorktree = true, postStatus = 200 } = {}) {
  const s = { project: { ...BASE_PROJECT }, posts: [], config: { worktree_isolation_enabled: globalWorktree }, postStatus };
  s.route = async (page) => {
    await page.route('**/*', (r) => {
      const req = r.request();
      const path = new URL(req.url()).pathname;
      if (path === '/' || path === '/index.html') return r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return r.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([s.project]) });
      if (path === '/api/config') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(s.config) });
      if (path === '/api/characters') return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (path === '/api/floor') return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === `/api/project/${PID}` && req.method() === 'POST') {
        const body = JSON.parse(req.postData() || '{}');
        s.posts.push(body);
        if (s.postStatus !== 200) {
          return r.fulfill({ status: s.postStatus, contentType: 'application/json',
            body: JSON.stringify({ error: 'Project safety settings are human-only; agent callers cannot write: merge_requires_review.' }) });
        }
        Object.assign(s.project, body);
        return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: PID }) });
      }
      return r.abort();
    });
  };
  return s;
}

async function boot(browser, server) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errs = []; page.on('pageerror', (e) => { if (!/cdn.jsdelivr.net/.test(String(e))) errs.push(String(e)); }); // the harness has no network: CDN-only imports (mermaid) are not ours
  await server.route(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#claydo-fab', { timeout: 15000 });
  await page.evaluate((id) => openProjectModal(id), PID);
  await page.waitForSelector(`#modal-layer .modal-window[data-modal-id="${PID}"]`, { timeout: 5000 });
  await page.waitForTimeout(200);
  return { ctx, page, errs };
}

async function openSafety(page) {
  await page.click(`#modal-layer .modal-window[data-modal-id="${PID}"] .modal-menu-btn`);
  const item = page.locator(`#modal-menu-${PID} .modal-menu-item`, { hasText: 'Safety' });
  if (await item.count() !== 1) return false;
  await item.click();
  await page.waitForSelector('#project-safety-overlay.visible #pss-merge_requires_review', { timeout: 5000 });
  return true;
}

const read = (page) => page.evaluate(() => {
  const v = (id) => document.getElementById(id);
  return {
    merge: v('pss-merge_requires_review').checked,
    cold: v('pss-cold_review_required').checked,
    verify: v('pss-verify_command').value,
    timeout: v('pss-verify_timeout_seconds').value,
    iso: v('pss-worktree_isolation').checked,
    branch: v('pss-worktree_base_branch').value,
    branchPlaceholder: v('pss-worktree_base_branch').placeholder,
    runtime: v('pss-worktree_shared_runtime').value,
    runtimePlaceholder: v('pss-worktree_shared_runtime').placeholder,
    text: document.getElementById('project-safety-overlay').innerText,
  };
});

const save = async (page) => {
  await page.click('#pss-save');
  await page.waitForTimeout(400);
};
const dialogOpen = (page) => page.evaluate(() => document.getElementById('project-safety-overlay')?.classList.contains('visible') === true);

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. defaults, no-op save, set everything, reload ────────────────────────
  {
    console.log('\n[defaults, then set every control]');
    const server = makeServer();
    const { ctx, page, errs } = await boot(browser, server);
    if (!(await openSafety(page))) {
      fail('project menu has no "Safety" entry that opens the dialog');
      await ctx.close();
      throw new Error('no Safety entry');
    }
    ok('menu has a Safety entry and it opens the dialog');

    let d = await read(page);
    eq('merge review off by default', d.merge, false);
    eq('cold review off by default', d.cold, false);
    eq('verify command empty by default', d.verify, '');
    eq('timeout shows the 1200s default', d.timeout, '1200');
    eq('worktree isolation shown on (global on, no opt-out)', d.iso, true);
    eq('base branch empty (inherits)', d.branch, '');
    /trunk/.test(d.branchPlaceholder) ? ok(`base branch placeholder names the sync-branch fallback (${d.branchPlaceholder})`)
      : fail(`base branch placeholder should name code_sync_branch "trunk", got "${d.branchPlaceholder}"`);
    eq('shared runtime empty (default list)', d.runtime, '');
    /node_modules/.test(d.runtimePlaceholder) && /\.venv/.test(d.runtimePlaceholder)
      ? ok('shared runtime placeholder lists the default paths')
      : fail(`shared runtime placeholder should list the defaults, got "${d.runtimePlaceholder}"`);
    !/switch is off/i.test(d.text) ? ok('no "global switch off" warning while the global switch is on')
      : fail('warning shown although the global worktree switch is on');

    await save(page);
    eq('Save with nothing changed sends no request', server.posts.length, 0);
    (await dialogOpen(page)) ? fail('dialog should close after a no-op Save') : ok('dialog closes after a no-op Save');

    await openSafety(page);
    await page.check('#pss-merge_requires_review');
    await page.check('#pss-cold_review_required');
    await page.fill('#pss-verify_command', 'npm test\npython -m pytest -q');
    await page.fill('#pss-verify_timeout_seconds', '600');
    await page.uncheck('#pss-worktree_isolation');
    await page.fill('#pss-worktree_base_branch', 'develop');
    await page.fill('#pss-worktree_shared_runtime', '.venv\nnode_modules\n');
    await save(page);

    eq('one POST sent', server.posts.length, 1);
    eq('POST body carries exactly the seven safety keys', server.posts[0], {
      merge_requires_review: true,
      cold_review_required: true,
      verify_command: ['npm test', 'python -m pytest -q'],
      verify_timeout_seconds: 600,
      worktree_isolation: false,
      worktree_base_branch: 'develop',
      worktree_shared_runtime: ['.venv', 'node_modules'],
    });
    (await dialogOpen(page)) ? fail('dialog should close after a successful Save') : ok('dialog closes after a successful Save');

    // The reloaded dialog must read the saved record back, not a stale copy.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#claydo-fab', { timeout: 15000 });
    await page.evaluate((id) => openProjectModal(id), PID);
    await page.waitForSelector(`#modal-layer .modal-window[data-modal-id="${PID}"]`, { timeout: 5000 });
    await page.waitForTimeout(200);
    await openSafety(page);
    d = await read(page);
    eq('reloaded: merge review', d.merge, true);
    eq('reloaded: cold review', d.cold, true);
    eq('reloaded: verify command', d.verify, 'npm test\npython -m pytest -q');
    eq('reloaded: timeout', d.timeout, '600');
    eq('reloaded: isolation (opted out)', d.iso, false);
    eq('reloaded: base branch', d.branch, 'develop');
    eq('reloaded: shared runtime', d.runtime, '.venv\nnode_modules');

    // Change ONE control: only that key is sent.
    await page.fill('#pss-verify_timeout_seconds', '90');
    await save(page);
    eq('changing one control sends only that key', server.posts[1], { verify_timeout_seconds: 90 });

    // Take everything back.
    await openSafety(page);
    await page.uncheck('#pss-merge_requires_review');
    await page.uncheck('#pss-cold_review_required');
    await page.fill('#pss-verify_command', '');
    await page.check('#pss-worktree_isolation');
    await page.fill('#pss-worktree_base_branch', '');
    await page.fill('#pss-worktree_shared_runtime', '');
    await save(page);
    eq('clearing sends null / true / "" / []', server.posts[2], {
      merge_requires_review: false,
      cold_review_required: false,
      verify_command: null,
      worktree_isolation: true,
      worktree_base_branch: '',
      worktree_shared_runtime: [],
    });

    // A single command goes out as a string, as hand-written records do.
    await openSafety(page);
    await page.fill('#pss-verify_command', 'make check');
    await save(page);
    eq('one command line is sent as a string', server.posts[3], { verify_command: 'make check' });

    eq('no page errors', errs, []);
    await ctx.close();
  }

  // ── 2. a stored list, an invalid stored timeout, validation ────────────────
  {
    console.log('\n[stored values + client validation]');
    const server = makeServer();
    server.project = { ...BASE_PROJECT, verify_command: ['a', 'b'], verify_timeout_seconds: 45, worktree_base_branch: 'release', worktree_shared_runtime: ['x/y'] };
    const { ctx, page, errs } = await boot(browser, server);
    await openSafety(page);
    let d = await read(page);
    eq('stored list shows one command per line', d.verify, 'a\nb');
    eq('stored timeout shown', d.timeout, '45');
    eq('stored base branch shown', d.branch, 'release');
    eq('stored shared runtime shown', d.runtime, 'x/y');

    await page.fill('#pss-verify_timeout_seconds', '0');
    await save(page);
    eq('timeout 0 sends nothing', server.posts.length, 0);
    (await dialogOpen(page)) ? ok('dialog stays open on an invalid timeout') : fail('dialog closed on an invalid timeout');
    /timeout/i.test(await page.locator('#pss-error').innerText()) ? ok('error names the timeout')
      : fail('no timeout error shown');

    await page.fill('#pss-verify_timeout_seconds', '45');
    await page.fill('#pss-worktree_base_branch', '--evil');
    await save(page);
    eq('branch starting with "-" sends nothing', server.posts.length, 0);
    /branch/i.test(await page.locator('#pss-error').innerText()) ? ok('error names the branch') : fail('no branch error shown');
    await page.fill('#pss-worktree_base_branch', 'has space');
    await save(page);
    eq('branch with whitespace sends nothing', server.posts.length, 0);
    eq('no page errors', errs, []);
    await ctx.close();
  }

  // ── 3. a refused write keeps the dialog open ───────────────────────────────
  {
    console.log('\n[server refuses the write]');
    const server = makeServer({ postStatus: 403 });
    const { ctx, page } = await boot(browser, server);
    await openSafety(page);
    await page.check('#pss-merge_requires_review');
    await save(page);
    eq('POST was attempted', server.posts.length, 1);
    (await dialogOpen(page)) ? ok('dialog stays open when the write is refused') : fail('dialog closed on a 403');
    /human-only/i.test(await page.locator('#pss-error').innerText()) ? ok("the server's error is shown in the dialog")
      : fail('403 error not shown');
    await ctx.close();
  }

  // ── 4. global worktree switch off ──────────────────────────────────────────
  {
    console.log('\n[global worktree switch off]');
    const server = makeServer({ globalWorktree: false });
    const { ctx, page } = await boot(browser, server);
    await openSafety(page);
    const d = await read(page);
    /switch is off/i.test(d.text) ? ok('dialog says the global worktree switch is off') : fail('no global-switch-off note');
    await ctx.close();
  }

  exitCode = bad ? 1 : 0;
} catch (e) {
  if (e.message !== 'no Safety entry') { console.error('✗ harness error: ' + (e.stack || e)); bad++; }
} finally {
  if (browser) await browser.close();
}
console.log(`\n${bad ? 'FAIL' : 'PASS'}: ${bad} failing check(s)`);
process.exit(bad ? 1 : exitCode);
