#!/usr/bin/env node
/**
 * Bring in your Claude Code projects (backlog ba3b73f9) — the UI half.
 *
 * Covers, at 1440 and 390 wide:
 *   A. First-run wizard: the 'import' step appears after "Protect your work",
 *      lists newest first, pre-ticks only the 5 most recent, the Add button
 *      counts the ticks, Add POSTs /api/project/<id> once per ticked project
 *      (the add-project endpoint, same body shape as the Create Project form),
 *      a refused project is reported by name with the server's reason and stays
 *      in the list, added ones leave the list, and the card never scrolls
 *      sideways.
 *   B. Nothing to import  -> the step is left out of the wizard.
 *   C. Scan fails         -> the step is left out (a first run is never held up).
 *   D. Create Project form -> "Bring in your existing projects" opens the same
 *      importer as a modal.
 *
 * Hermetic like first-run-setup-gate.mjs: route-mocked /api/*, real index.html
 * and static/js/*.js, no running server.
 *
 * RUN   cd tools/smoke && node claude-import.mjs
 * Exit 0 = every assertion holds; 1 = a regression or a page error fired.
 * Screenshots land in docs/screenshots/claude-import-*.png (SHOTS=0 to skip).
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'screenshots');
const SHOTS = process.env.SHOTS !== '0';
if (SHOTS) mkdirSync(SHOT_DIR, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};
const ONE_PROVIDER_OK = {
  providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }],
  default: 'claude',
};

const NAMES = ['mission-control', 'clayrune-cloud', 'dotfiles', 'resume-site', 'trading-bot', 'my_notes', 'old-experiment', 'a-very-long-project-folder-name-that-keeps-going'];
const NOW = Date.now();
const CANDIDATES = NAMES.map((name, i) => ({
  path: `/home/smoke/work/${name}`, name, id: name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''),
  last_activity: new Date(NOW - (i + 1) * 86400 * 1000 * (i + 1)).toISOString().replace(/\.\d+Z$/, 'Z'),
  session_count: 12 - i,
}));
const SCAN_OK = { ok: true, candidates: CANDIDATES, total: CANDIDATES.length, truncated: false,
  skipped: { missing: 3, registered: 1, worktree: 2, install_dir: 0, home_or_root: 1, no_cwd: 0 } };

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

let browser;
let n = 0;

async function scenario(name, { width, scan, refuse = {}, setupDone = false }, run) {
  n++;
  console.log(`\n[${n}] ${name} @${width}`);
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 800 : 900 }, isMobile: width < 600, hasTouch: width < 600 });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const created = [];
  const config = { setup_completed: setupDone, default_provider: 'claude', agent_model: 'tier:balanced' };
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return json(route, [CLAYRUNE_PROJECT]);
    if (path === '/api/config') return json(route, req.method() === 'PUT' ? {} : config);
    if (path === '/api/setup/complete') return json(route, { ok: true, setup_completed: true });
    if (path === '/api/claude-import/scan') {
      if (scan === 'fail') return json(route, { ok: false, error: 'disk on fire' }, 500);
      return json(route, scan);
    }
    const m = path.match(/^\/api\/project\/([^/]+)$/);
    if (m && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      created.push({ id: decodeURIComponent(m[1]), body });
      if (refuse[body.project_path]) return json(route, { error: refuse[body.project_path] }, 409);
      return json(route, { ok: true, id: decodeURIComponent(m[1]) });
    }
    if (path === '/api/characters') return json(route, []);
    if (path === '/api/agent/providers') return json(route, ONE_PROVIDER_OK);
    if (path === '/api/local-auth/status') return json(route, { configured: false });
    if (path === '/api/system/update/status') return json(route, { is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] });
    return route.abort();
  });

  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await run(page, created);
  } finally {
    if (pageErrors.length) fail(`[${name}] uncaught exception(s): ${pageErrors.join(' | ')}`);
    await ctx.close();
  }
}

const title = (page) => page.evaluate(() => (document.querySelector('#setup-overlay .wt-title') || {}).textContent || null);
const shot = async (page, file) => { if (SHOTS) await page.screenshot({ path: resolve(SHOT_DIR, file) }); };

// Welcome -> Make it yours -> phone -> detail -> protect -> (import | tour).
async function walkToAfterProtect(page) {
  await page.waitForSelector('#setup-overlay .wt-title', { timeout: 5000 });
  await page.click('#setup-overlay .wt-btn-primary');                       // Get started (connections skipped)
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours');
  await page.click('#setup-overlay .wt-btn-primary');                       // -> phone
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Use Clayrune from your phone?');
  await page.click('#setup-overlay button:has-text("Not now")');            // -> detail
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'How much detail do you want to see?');
  await page.click('#setup-overlay .wt-btn-primary');                       // -> protect
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Protect your work');
  await page.click('#setup-overlay .wt-btn-primary');                       // -> next
  await page.waitForTimeout(200);
}

const rows = (page) => page.evaluate(() => Array.from(document.querySelectorAll('#claude-import-root .ci-row')).map(r => ({
  name: r.querySelector('.ci-name').textContent, on: r.querySelector('input').checked })));
const modalRows = (page) => page.evaluate(() => Array.from(document.querySelectorAll('[data-modal-id="__claude_import"] .ci-row')).map(r => ({
  name: r.querySelector('.ci-name').textContent, on: r.querySelector('input').checked })));
const addLabel = (page) => page.evaluate(() => (document.querySelector('#claude-import-root .ci-actions button') || {}).textContent || null);
const overflowX = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); return e ? e.scrollWidth - e.clientWidth : -1; }, sel);

try {
  browser = await chromium.launch();

  for (const width of [1440, 390]) {
    const tag = width === 1440 ? 'desktop' : 'phone';

    await scenario('wizard: import step lists, ticks 5, adds through /api/project/<id>', { width, scan: SCAN_OK,
      refuse: { '/home/smoke/work/dotfiles': 'Path already used by project "Dots". Each project needs its own folder.' } }, async (page, created) => {
      await walkToAfterProtect(page);
      check(await title(page) === 'Bring in your Claude Code projects', 'import step follows "Protect your work"', `expected import step, got: ${await title(page)}`);
      const progress = await page.evaluate(() => document.querySelector('#setup-overlay .setup-band-progress').textContent);
      ok(`progress band reads: ${progress}`);
      await page.waitForSelector('#claude-import-root .ci-row');
      const r = await rows(page);
      check(r.length === 8 && r.map(x => x.name).join() === NAMES.join(), 'all 8 rows, in the order the server returned (newest first)', `rows: ${r.map(x => x.name)}`);
      check(r.filter(x => x.on).length === 5 && r.slice(0, 5).every(x => x.on), 'exactly the 5 most recent are ticked', `ticked: ${r.filter(x => x.on).map(x => x.name)}`);
      check(await addLabel(page) === 'Add 5 projects', 'button reads "Add 5 projects"', `button: ${await addLabel(page)}`);
      const skipped = await page.evaluate(() => (document.querySelector('#claude-import-root .ci-skipped') || {}).textContent || '');
      check(/3 folders that no longer exist/.test(skipped) && /2 Clayrune agent worktrees/.test(skipped), 'says what it left out', `skipped line: ${skipped}`);
      await shot(page, `claude-import-wizard-${tag}-${width}.png`);

      // untick the 2nd (clayrune-cloud), tick the 6th (my_notes) -> still 5
      await page.click('#claude-import-root .ci-row:nth-child(2) input');
      check(await addLabel(page) === 'Add 4 projects', 'unticking one updates the count', `button: ${await addLabel(page)}`);
      await page.click('#claude-import-root .ci-row:nth-child(6) input');
      check(await addLabel(page) === 'Add 5 projects', 'ticking another updates the count', `button: ${await addLabel(page)}`);

      if (width < 600) check(await overflowX(page, '#setup-overlay .wt-card') <= 1, 'card does not scroll sideways at 390', `card overflowX=${await overflowX(page, '#setup-overlay .wt-card')}`);

      await page.click('#claude-import-root .ci-actions button');
      await page.waitForSelector('#claude-import-root .ci-results');
      check(created.length === 5, 'one POST per ticked project', `POSTs: ${created.map(c => c.id)}`);
      const want = ['mission_control', 'dotfiles', 'resume_site', 'trading_bot', 'my_notes'];
      check(created.map(c => c.id).join() === want.join(), 'POSTs go to /api/project/<suggested id>', `ids: ${created.map(c => c.id)}`);
      const b = created.find(c => c.id === 'mission_control').body;
      check(b.name === 'mission-control' && b.project_path === '/home/smoke/work/mission-control' && b.domain === 'general' && b.status === 'active',
        'body has name, project_path, domain, status (the Create Project shape)', `body: ${JSON.stringify(b)}`);
      const res = await page.evaluate(() => document.querySelector('#claude-import-root .ci-results').textContent);
      check(/Added 4 projects: mission-control, resume-site, trading-bot, my_notes/.test(res), 'reports what was added', `results: ${res}`);
      check(/dotfiles not added: Path already used by project "Dots"/.test(res), 'reports the refused project with the server reason', `results: ${res}`);
      const after = await rows(page);
      check(after.map(x => x.name).join() === ['clayrune-cloud', 'dotfiles', 'old-experiment', 'a-very-long-project-folder-name-that-keeps-going'].join(),
        'added projects left the list; the refused one stayed', `rows after: ${after.map(x => x.name)}`);
      await shot(page, `claude-import-wizard-${tag}-${width}-added.png`);

      await page.click('#setup-overlay .wt-btn-primary');                   // Next
      await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Take the tour?');
      ok('Next continues to the tour offer');
    });

    await scenario('wizard: nothing to import -> step left out', { width,
      scan: { ok: true, candidates: [], total: 0, truncated: false, skipped: { missing: 0, registered: 0, worktree: 0, install_dir: 0, home_or_root: 0, no_cwd: 0 } } }, async (page) => {
      await walkToAfterProtect(page);
      check(await title(page) === 'Take the tour?', 'goes straight to the tour offer', `title: ${await title(page)}`);
    });

    await scenario('wizard: scan fails -> step left out', { width, scan: 'fail' }, async (page) => {
      await walkToAfterProtect(page);
      check(await title(page) === 'Take the tour?', 'goes straight to the tour offer', `title: ${await title(page)}`);
    });

    await scenario('Create Project form links to the importer modal', { width, scan: SCAN_OK, setupDone: true }, async (page, created) => {
      await page.waitForFunction(() => typeof window.openNewProjectForm === 'function');
      await page.evaluate(() => window.openNewProjectForm());
      await page.waitForSelector('[data-modal-id="__new_project"] a:has-text("Bring in your existing projects")');
      await page.click('[data-modal-id="__new_project"] a:has-text("Bring in your existing projects")');
      await page.waitForSelector('[data-modal-id="__claude_import"] .ci-row');
      check(await page.evaluate(() => !document.querySelector('[data-modal-id="__new_project"]')), 'the Create Project form closed', 'Create Project form still open');
      const r = await modalRows(page);
      check(r.length === 8 && r.filter(x => x.on).length === 5, 'modal lists 8, 5 ticked', `rows: ${JSON.stringify(r)}`);
      if (width < 600) check(await overflowX(page, '[data-modal-id="__claude_import"] .modal-content') <= 1, 'modal does not scroll sideways at 390', 'modal overflows sideways');
      await shot(page, `claude-import-modal-${tag}-${width}.png`);
      await page.click('[data-modal-id="__claude_import"] .ci-actions button');
      await page.waitForSelector('[data-modal-id="__claude_import"] .ci-results');
      check(created.length === 5, 'modal Add creates the 5 ticked projects through the same endpoint', `POSTs: ${created.length}`);
    });
  }
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} assertion(s) failed` : '\nall assertions passed');
process.exit(bad ? 1 : 0);
