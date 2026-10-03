#!/usr/bin/env node
/**
 * Regression: a person's "Working" state in a project's Channel rail comes only
 * from THAT project's sessions (Ron, 2026-10-02, "shadow thinking").
 *
 * updateRailRowStatus (conversation.js) patches the Channel roster row in place
 * on turn_start/turn_complete. It matched `.channel-row[data-char-key=...]`
 * document-wide, so with two project modals open side by side, Dave working in
 * Mission Control also lit Dave's row in Find Ron a Job: a bench row showing
 * Working until the next full rebuild. Rows now carry data-project-id and the
 * patch is scoped to the session's project.
 *
 * Hermetic headless boot, same shape as channel-mode-roster.mjs.
 * RUN: cd tools/smoke && node channel-working-project-scope.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID_A = 'smoke_proj_a';   // Dave works here
const PID_B = 'smoke_proj_b';   // Dave sits on the bench here

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID_A, 'Proj A'), fixtureProject(PID_B, 'Proj B')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  pageErrors.length ? pageErrors.forEach((e) => fail('boot error: ' + e)) : ok('app booted clean');

  const DAVE = { name: 'dave', scope: 'global', display_name: 'dave', agent_name: 'Dave', avatar: 'fig:smith' };
  const DAVE_KEY = 'global:dave';
  await page.evaluate(({ a, b, dave }) => {
    // Dave has history in BOTH projects (so he is on both rosters); each chat
    // is completed. No live session anywhere yet.
    for (const [pid, n] of [[a, 'a'], [b, 'b']]) {
      conversationsCache[pid] = [{ claude_session_id: 'csid-' + n, mc_session_id: 'mc-' + n, character: dave, mtime: 1000, ts_relative: '2h ago', status: 'completed', turns: 3, label: 'chat ' + n, first_user: 'chat ' + n, last_user: 'chat ' + n }];
      agentStatusCache['mc-' + n] = { status: 'completed', task: '', projectId: pid, startedAt: '', claudeSessionId: 'csid-' + n, character: dave };
    }
    openProjectModal(a);
    openProjectModal(b);
  }, { a: PID_A, b: PID_B, dave: DAVE });

  for (const pid of [PID_A, PID_B]) {
    await page.waitForSelector(`.modal-window[data-modal-id="${pid}"] .agent-rail`, { timeout: 5000 });
    // DOM click: the later modal stacks over the earlier one (same as Ron's
    // side-by-side view), so a pointer click on the earlier rail is intercepted.
    await page.evaluate((id) => {
      const btn = Array.from(document.querySelectorAll(`.modal-window[data-modal-id="${id}"] .rail-mode-btn`)).find((b) => b.textContent.trim() === 'Channel');
      btn.click();
    }, pid);
  }
  const row = (pid) => `.modal-window[data-modal-id="${pid}"] .channel-row[data-char-key="${DAVE_KEY}"]`;
  const slot = (pid) => page.$eval(row(pid) + ' .conv-time', (el) => ({ word: !!el.querySelector('.act-word'), text: el.textContent.trim() })).catch((e) => ({ err: String(e) }));

  const a0 = await slot(PID_A), b0 = await slot(PID_B);
  (a0.word === false && b0.word === false && !a0.err && !b0.err)
    ? ok('both Dave rows start idle (relative time, no Working word)') : fail('precondition: ' + JSON.stringify({ a0, b0 }));

  // Dave starts a turn in project A only: same code path turn_start uses.
  await page.evaluate(() => {
    agentStatusCache['mc-a'].status = 'running';
    updateRailRowStatus('mc-a');
  });
  const a1 = await slot(PID_A), b1 = await slot(PID_B);
  a1.word ? ok('project A (Dave working) shows Working') : fail('project A row did not show Working: ' + JSON.stringify(a1));
  !b1.word ? ok('project B (Dave on the bench) does NOT show Working, no cross-project leak')
           : fail('project B row shows Working though Dave only works in A: ' + JSON.stringify(b1));

  // Turn ends: A clears, B untouched.
  await page.evaluate(() => { agentStatusCache['mc-a'].status = 'completed'; updateRailRowStatus('mc-a'); });
  const a2 = await slot(PID_A), b2 = await slot(PID_B);
  (!a2.word && !b2.word) ? ok('after turn_complete neither row shows Working') : fail('after complete: ' + JSON.stringify({ a2, b2 }));

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught exception: ' + e));
  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS: Channel Working state is scoped to the session project.' : `\n❌ FAIL: ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
