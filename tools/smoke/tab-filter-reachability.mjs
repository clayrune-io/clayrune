#!/usr/bin/env node
/**
 * Confirm-then-fix: the project modal's "Filter..." box for Agent Log,
 * Documents and Activity was rendered inside .modal-tab-bar, which is
 * display:none !important at every breakpoint (those tabs are reached via
 * the menu dropdown, not the hidden bar) — so the filter existed in the DOM
 * but was permanently unreachable, on desktop and mobile alike.
 *
 * Fix moved each tab's filter input into that tab's own toolbar (same
 * pattern as the Backlog tab's .backlog-search, MC-955), scoped via
 * ".modal-tab-content.active .modal-tab-search" so applyTabFilter/
 * clearTabSearch always touch the ON-SCREEN input, not whichever tab
 * happens to render first in the DOM.
 *
 * RETARGETED (2026-09-21): this test used to drive the REAL running server
 * at localhost:5199 — whatever frontend a developer happens to have running
 * there, not the static/ under test. Same trap boot-smoke.mjs and
 * backlog-search-tab-isolation.mjs already guard against: it fails when the
 * live server is stopped or serving an older checkout, and passes or fails
 * on code this repo didn't write. Retargeted to the same hermetic harness
 * those two use — fulfills the real static/index.html + every extracted
 * /static/js/*.js module against canned API responses via Playwright route
 * interception, no running server, no network. Passes with the live server
 * stopped or serving old code.
 *
 * RUN: node tab-filter-reachability.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const rd = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');

const INDEX_HTML = rd('static', 'index.html');
const APP_CSS = rd('static', 'css', 'app.css');
const BEACON_CSS = rd('static', 'css', 'beacon.css');

// Same module list as boot-smoke.mjs / backlog-search-tab-isolation.mjs's
// STATIC_MAP — every extracted /static/js/*.js must be fulfilled or the
// hermetic harness aborts its request and the SPA boots without that
// feature (here, without the modal/tab wiring under test).
const JS_MODULES = [
  'claydo.js', 'mobile-pairing.js', 'walkthrough.js', 'skills-panel.js', 'media.js',
  'settings-drill.js', 'settings-sections.js', 'terminal.js', 'mermaid.js',
  'search-chats.js', 'backlog-actions.js', 'cross-backlog.js', 'scheduler.js',
  'schedule-calendar.js', 'automation-suggestions.js', 'mcp.js', 'secrets-panel.js',
  'system-status.js', 'update-power.js', 'provider-auth.js', 'schedule-banner.js',
  'provider-settings.js', 'process-manager.js', 'cross-hivemind.js', 'feed.js',
  'beacon.js', 'mobile.js', 'project-actions.js', 'composer-extras.js',
  'slash-autocomplete.js', 'mention-autocomplete.js', 'appearance.js',
  'project-forms.js', 'interactions.js', 'render-core.js', 'modal-manager.js',
  'agent-console.js', 'floor.js', 'hivemind.js', 'agent-log.js', 'resume-preview.js',
  'conversation.js', 'rich-text.js', 'team-card.js', 'cross-social.js', 'desk.js',
  'workflow-builder.js', 'first-run.js',
];
const STATIC_MAP = { '/static/css/app.css': ['text/css; charset=utf-8', APP_CSS],
  '/static/css/beacon.css': ['text/css; charset=utf-8', BEACON_CSS] };
for (const name of JS_MODULES) {
  STATIC_MAP[`/static/js/${name}`] = ['text/javascript; charset=utf-8', rd('static', 'js', name)];
}

const PROJECT_ID = 'smoke_tabfilter';

// project_path must be truthy — agentLogPanelHTML (agent-log.js) returns ''
// for a falsy project_path, which would render the Agent Log tab with no
// filter input at all and the corresponding checkTab() would fail for the
// wrong reason (missing panel, not the bug under test).
const PROJECT = {
  id: PROJECT_ID, name: 'Tab Filter Smoke', status: 'active', domain: 'general',
  emoji: '🧪', description: 'Fixture project for the tab-filter reachability smoke test.',
  summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [
    { msg: 'First activity entry — deploy finished', ts_relative: '2h ago' },
    { msg: 'Second activity entry — backlog groomed', ts_relative: '1h ago' },
  ],
  backlog: [], project_path: '/tmp/smoke-tabfilter', last_updated: '2026-09-21T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed',
  distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};
const PROJECTS_JSON = [PROJECT];

const AGENT_LOG_ENTRIES = [
  { session_id: 'sess-1', task: 'Fix the composer paste handler', summary: 'Landed 1a2b3c4',
    status: 'completed', provider: 'claude', ts_relative: '2h ago', started_relative: '2h ago' },
  { session_id: 'sess-2', task: 'Retarget smoke harness', summary: 'In review',
    status: 'completed', provider: 'claude', ts_relative: '1h ago', started_relative: '1h ago' },
];

const DOCUMENTS = [
  { path: 'docs/PLAN_ONE.md', title: 'Plan One', kind: 'plan', ts_relative: '2h ago',
    task: 'First plan', filename: 'PLAN_ONE.md', deletable: true },
  { path: 'docs/DOC_TWO.md', title: 'Doc Two', kind: 'doc', ts_relative: '1h ago',
    location: 'docs/DOC_TWO.md', filename: 'DOC_TWO.md', deletable: false },
];

const ORIGIN = 'http://mc.smoke.test';

function fulfillStaticOrAbort(route) {
  const path = new URL(route.request().url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC_MAP[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects')
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROJECTS_JSON) });
  if (path === '/api/config')
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  // openProjectModal()'s lazy per-project fetches (Promise.all in modal-manager.js)
  if (path === `/api/project/${PROJECT_ID}/backlog`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  if (path === `/api/project/${PROJECT_ID}/agent/status`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"sessions":[]}' });
  if (path === `/api/project/${PROJECT_ID}/terminal/status`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"sessions":[]}' });
  if (path === `/api/project/${PROJECT_ID}/social/queue`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  // switchModalTab('agent-log') → loadAgentLog() (the "Completed Sessions" list
  // that carries the filter input) and loadDeliveryStatus() (a SEPARATE panel
  // that renders its own .agent-log-entry rows when it has pending items —
  // stubbed empty here so it can't inflate checkTab()'s entry count).
  if (path === `/api/project/${PROJECT_ID}/agent/log`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AGENT_LOG_ENTRIES) });
  if (path === `/api/project/${PROJECT_ID}/agent/delegation/status-list`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[],"total":0,"limit":25,"offset":0}' });
  if (path === `/api/project/${PROJECT_ID}/documents`)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DOCUMENTS) });
  return route.abort();
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function openProject(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 20000 });
  await page.evaluate((pid) => window.openProjectModal(pid), PROJECT_ID);
  await page.waitForTimeout(600);
}

async function checkTab(page, pid, tab, entrySelector, label) {
  await page.evaluate((args) => window.switchModalTab(args.p, args.t), { p: pid, t: tab });
  await page.waitForTimeout(400);

  const input = page.locator('.modal-tab-content.active .modal-tab-search input');
  const count = await input.count();
  if (count !== 1) { fail(`${label}: expected exactly 1 filter input, found ${count}`); return; }

  const box = await input.boundingBox();
  const display = await input.evaluate((el) => getComputedStyle(el).display);
  if (display === 'none' || !box || box.width === 0 || box.height === 0) {
    fail(`${label}: filter input not visible (display=${display}, box=${JSON.stringify(box)})`);
    return;
  }
  ok(`${label}: filter visible (display=${display}, box=${Math.round(box.width)}x${Math.round(box.height)})`);

  const total = await page.$$eval(entrySelector, (e) => e.length);
  if (total === 0) { fail(`${label}: expected seeded entries, found 0 — fixture wiring broke`); return; }

  await input.fill('zzzznomatchzzzz');
  await page.waitForTimeout(300);
  const visibleAfter = await page.$$eval(entrySelector, (e) => e.filter((x) => getComputedStyle(x).display !== 'none').length);
  visibleAfter === 0 ? ok(`${label}: nonsense query hides all ${total} entries`) : fail(`${label}: expected 0 visible after filtering, got ${visibleAfter}`);

  await input.fill('');
  await page.waitForTimeout(300);
  const visibleCleared = await page.$$eval(entrySelector, (e) => e.filter((x) => getComputedStyle(x).display !== 'none').length);
  visibleCleared === total ? ok(`${label}: clearing restores all ${total} entries`) : fail(`${label}: expected ${total} after clear, got ${visibleCleared}`);
}

async function runAtViewport(browser, width, height, label) {
  console.log(`\n-- ${label} (${width}x${height}) --`);
  const ctx = await browser.newContext({ viewport: { width, height } });
  try {
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await page.route('**/*', fulfillStaticOrAbort);

    await openProject(page);
    ok(`opened project "${PROJECT_ID}"`);

    await checkTab(page, PROJECT_ID, 'agent-log', '.agent-log-entry', 'agent-log');
    await checkTab(page, PROJECT_ID, 'activity', '.log-entry', 'activity');
    await checkTab(page, PROJECT_ID, 'documents', '.plan-history-card', 'documents');

    // Same allowance as boot-smoke.mjs/backlog-search-tab-isolation.mjs: the
    // hermetic harness has no network, so mermaid.js's dynamic import from the
    // jsdelivr CDN always fails here — not a regression signal.
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0 ? ok('no uncaught JS errors')
      : uncaught.forEach((e) => fail('uncaught: ' + e));
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await runAtViewport(browser, 1440, 900, 'desktop');
  await runAtViewport(browser, 390, 844, 'mobile');
} finally {
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
